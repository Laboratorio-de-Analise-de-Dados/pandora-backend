from types import SimpleNamespace

import numpy as np
import pandas as pd
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import User
from analytics.models import (
    AnalysisBranch,
    AnalysisRevision,
    DashboardModel,
    GateModel,
)
from analytics.tasks import calculate_cytometry_metrics, recalculate_gate_analysis
from fcs_parser.models import (
    ExperimentModel,
    FileDataModel,
    FileModel,
    SubsampleModel,
)


class GateFixtureMixin:
    """Experimento com três amostras e uma família de cópias de `P1`."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="dono", email="dono@pandora.test", password="senha-forte-123"
        )
        self.outsider = User.objects.create_user(
            username="outro", email="outro@pandora.test", password="senha-forte-123"
        )
        self.experiment = ExperimentModel.objects.create(
            title="exp", type="tipo", created_by=self.user
        )
        self.other_experiment = ExperimentModel.objects.create(
            title="exp2", type="tipo", created_by=self.user
        )
        self.file_a = self._file(self.experiment, "a.fcs")
        self.file_b = self._file(self.experiment, "b.fcs")
        self.file_c = self._file(self.experiment, "c.fcs")
        self.file_other_exp = self._file(self.other_experiment, "d.fcs")

        self.source = self._gate(self.file_a, "P1")
        self.child = self._gate(self.file_a, "P2", parent=self.source)
        self.copy_b = self._gate(self.file_b, "P1", copied_from=self.source)
        self.copy_c = self._gate(self.file_c, "P1", copied_from=self.source)
        self.copy_other_exp = self._gate(
            self.file_other_exp, "P1", copied_from=self.source
        )

        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def _file(self, experiment, name):
        file_model, _ = FileModel.objects.get_or_create(
            experiment=experiment, defaults={"file_name": f"{experiment.title}.zip"}
        )
        return FileDataModel.objects.create(
            experiment=experiment, file_name=name, headers=[], file=file_model
        )

    def _gate(self, file_data, name, parent=None, copied_from=None, color="#111111"):
        dashboard = DashboardModel.objects.create(
            name=f"{file_data.file_name}-{name}-{parent.id if parent else 0}",
            file_data=file_data,
        )
        return GateModel.objects.create(
            file_data=file_data,
            name=name,
            dashboard=dashboard,
            parent=parent,
            copied_from=copied_from,
            color=color,
        )

    def _delete_batch(self, **payload):
        return self.client.post("/analytics/gate/delete-batch", payload, format="json")

    def _patch_gate(self, gate, **payload):
        return self.client.patch(f"/analytics/gate/{gate.id}", payload, format="json")


class GateScopeTestCase(GateFixtureMixin, TestCase):
    """BE-03/BE-04: exclusão em lote e propagação de nome/cor por escopo."""

    def test_scope_file_deletes_only_current_sample(self):
        res = self._delete_batch(source_gate_ids=[self.source.id])

        self.assertEqual(res.status_code, 200)
        self.assertFalse(GateModel.objects.filter(id=self.source.id).exists())
        self.assertFalse(GateModel.objects.filter(id=self.child.id).exists())
        self.assertTrue(GateModel.objects.filter(id=self.copy_b.id).exists())
        self.assertTrue(GateModel.objects.filter(id=self.copy_c.id).exists())

    def test_scope_experiment_deletes_copies_and_keeps_source(self):
        res = self._delete_batch(source_gate_ids=[self.source.id], scope="experiment")

        self.assertEqual(res.status_code, 200)
        self.assertTrue(GateModel.objects.filter(id=self.source.id).exists())
        self.assertFalse(GateModel.objects.filter(id=self.copy_b.id).exists())
        self.assertFalse(GateModel.objects.filter(id=self.copy_c.id).exists())
        self.assertTrue(GateModel.objects.filter(id=self.copy_other_exp.id).exists())

    def test_scope_experiment_respects_target_files_and_include_source(self):
        res = self._delete_batch(
            source_gate_ids=[self.source.id],
            scope="experiment",
            target_file_data_ids=[self.file_b.id],
            include_source=True,
        )

        self.assertEqual(res.status_code, 200)
        self.assertFalse(GateModel.objects.filter(id=self.source.id).exists())
        self.assertFalse(GateModel.objects.filter(id=self.copy_b.id).exists())
        self.assertTrue(GateModel.objects.filter(id=self.copy_c.id).exists())

    def test_scope_experiment_ignores_disabled_samples(self):
        self.file_c.active = False
        self.file_c.save(update_fields=["active"])

        res = self._delete_batch(source_gate_ids=[self.source.id], scope="experiment")

        self.assertEqual(res.status_code, 200)
        self.assertFalse(GateModel.objects.filter(id=self.copy_b.id).exists())
        self.assertTrue(GateModel.objects.filter(id=self.copy_c.id).exists())

    def test_non_recursive_delete_with_subgates_is_rejected(self):
        res = self._delete_batch(source_gate_ids=[self.source.id], recursive=False)

        self.assertEqual(res.status_code, 409)
        self.assertTrue(GateModel.objects.filter(id=self.source.id).exists())

    def test_target_files_rejected_in_file_scope(self):
        res = self._delete_batch(
            source_gate_ids=[self.source.id],
            target_file_data_ids=[self.file_b.id],
        )

        self.assertEqual(res.status_code, 400)

    def test_delete_batch_requires_permission(self):
        self.client.force_authenticate(self.outsider)

        res = self._delete_batch(source_gate_ids=[self.source.id])

        self.assertEqual(res.status_code, 404)
        self.assertTrue(GateModel.objects.filter(id=self.source.id).exists())

    def test_patch_file_scope_does_not_propagate(self):
        res = self._patch_gate(self.source, name="CD4+", color="#ff0000")

        self.assertEqual(res.status_code, 200)
        self.copy_b.refresh_from_db()
        self.assertEqual(self.copy_b.name, "P1")
        self.assertEqual(self.copy_b.color, "#111111")

    def test_patch_experiment_scope_propagates_name_and_color(self):
        res = self._patch_gate(
            self.source, name="CD4+", color="#ff0000", scope="experiment"
        )

        self.assertEqual(res.status_code, 200)
        self.copy_b.refresh_from_db()
        self.copy_c.refresh_from_db()
        self.copy_other_exp.refresh_from_db()
        self.assertEqual(self.copy_b.name, "CD4+")
        self.assertEqual(self.copy_b.color, "#ff0000")
        self.assertEqual(self.copy_c.name, "CD4+")
        self.assertEqual(self.copy_other_exp.name, "P1")
        self.assertCountEqual(
            res.data["propagated_gate_ids"], [self.copy_b.id, self.copy_c.id]
        )
        self.assertEqual(res.data["conflicts"], [])

    def test_patch_experiment_scope_reports_name_conflict(self):
        self._gate(self.file_b, "CD4+")

        res = self._patch_gate(self.source, name="CD4+", scope="experiment")

        self.assertEqual(res.status_code, 200)
        self.copy_b.refresh_from_db()
        self.copy_c.refresh_from_db()
        self.assertEqual(self.copy_b.name, "P1")
        self.assertEqual(self.copy_c.name, "CD4+")
        self.assertEqual(len(res.data["conflicts"]), 1)
        self.assertEqual(res.data["conflicts"][0]["gate_id"], self.copy_b.id)

    def test_patch_coordinates_detaches_copy_from_family(self):
        res = self._patch_gate(
            self.copy_b,
            gate_coordinates={
                "type": "rectangle",
                "x_axis": "FSC-A",
                "y_axis": "SSC-A",
                "startX": 0,
                "endX": 1,
                "startY": 0,
                "endY": 1,
            },
        )

        self.assertEqual(res.status_code, 200)
        self.copy_b.refresh_from_db()
        self.assertIsNone(self.copy_b.copied_from_id)

        res = self._patch_gate(self.source, name="CD4+", scope="experiment")

        self.assertEqual(res.status_code, 200)
        self.copy_b.refresh_from_db()
        self.copy_c.refresh_from_db()
        self.assertEqual(self.copy_b.name, "P1")
        self.assertEqual(self.copy_c.name, "CD4+")
        self.assertEqual(res.data["propagated_gate_ids"], [self.copy_c.id])

    def test_patch_name_and_color_keeps_copy_attached(self):
        res = self._patch_gate(self.copy_b, name="CD8+", color="#00ff00")

        self.assertEqual(res.status_code, 200)
        self.copy_b.refresh_from_db()
        self.assertEqual(self.copy_b.copied_from_id, self.source.id)

    def test_patch_coordinates_experiment_scope_propagates_and_keeps_family(self):
        coords = {
            "type": "rectangle",
            "x_axis": "FSC-A",
            "y_axis": "SSC-A",
            "startX": 0,
            "endX": 1,
            "startY": 0,
            "endY": 1,
        }

        res = self._patch_gate(self.copy_b, gate_coordinates=coords, scope="experiment")

        self.assertEqual(res.status_code, 200)
        self.copy_b.refresh_from_db()
        self.copy_c.refresh_from_db()
        self.source.refresh_from_db()
        self.assertEqual(self.copy_b.copied_from_id, self.source.id)
        self.assertEqual(self.copy_c.gate_coordinates, coords)
        self.assertEqual(self.source.gate_coordinates, coords)
        self.assertCountEqual(
            res.data["propagated_gate_ids"], [self.source.id, self.copy_c.id]
        )

    def test_apply_dry_run_reports_conflicts_without_writing(self):
        existing = self._gate(self.file_b, "P9")
        source = self._gate(self.file_a, "P9")

        res = self.client.post(
            "/analytics/gate/apply",
            {
                "source_gate_ids": [source.id],
                "target_file_data_ids": [self.file_b.id],
                "dry_run": True,
            },
            format="json",
        )

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["created"], 0)
        self.assertEqual([c["gate_id"] for c in res.data["conflicts"]], [existing.id])
        existing.refresh_from_db()
        self.assertIsNone(existing.copied_from_id)

    def test_apply_replace_overwrites_in_place_and_keeps_subgates(self):
        existing = self._gate(self.file_b, "P9")
        subgate = self._gate(self.file_b, "P9.1", parent=existing)
        coords = {
            "type": "rectangle",
            "x_axis": "FSC-A",
            "y_axis": "SSC-A",
            "startX": 2,
            "endX": 3,
            "startY": 2,
            "endY": 3,
        }
        source = self._gate(self.file_a, "P9", color="#abcdef")
        source.gate_coordinates = coords
        source.save(update_fields=["gate_coordinates"])

        res = self.client.post(
            "/analytics/gate/apply",
            {
                "source_gate_ids": [source.id],
                "target_file_data_ids": [self.file_b.id],
                "on_conflict": "replace",
            },
            format="json",
        )

        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data["replaced"], 1)
        existing.refresh_from_db()
        self.assertEqual(existing.gate_coordinates, coords)
        self.assertEqual(existing.color, "#abcdef")
        self.assertEqual(existing.copied_from_id, source.id)
        self.assertTrue(GateModel.objects.filter(id=subgate.id).exists())

    def test_apply_records_author_of_the_copies(self):
        source = self._gate(self.file_a, "P8")

        res = self.client.post(
            "/analytics/gate/apply",
            {
                "source_gate_ids": [source.id],
                "target_file_data_ids": [self.file_b.id],
            },
            format="json",
        )

        self.assertEqual(res.status_code, 201)
        copy = GateModel.objects.get(file_data=self.file_b, name="P8")
        self.assertEqual(copy.created_by_id, self.user.id)

    def test_apply_requires_both_lists(self):
        res = self.client.post("/analytics/gate/apply", {}, format="json")

        self.assertEqual(res.status_code, 400)

    def test_apply_rejects_unknown_on_conflict(self):
        # Antes do serializer, um valor fora das choices caía
        # silenciosamente no rename; agora é 400 (ADR-0009).
        source = self._gate(self.file_a, "P8")

        res = self.client.post(
            "/analytics/gate/apply",
            {
                "source_gate_ids": [source.id],
                "target_file_data_ids": [self.file_b.id],
                "on_conflict": "bogus",
            },
            format="json",
        )

        self.assertEqual(res.status_code, 400)

    def test_gate_list_exposes_author_name(self):
        self.source.created_by = self.user
        self.source.save(update_fields=["created_by"])

        res = self.client.get(f"/analytics/gate/{self.source.id}")

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["created_by_name"], self.user.username)

    def test_build_tree_exposes_author(self):
        self.user.first_name = "Ana"
        self.user.last_name = "Souza"
        self.user.save(update_fields=["first_name", "last_name"])
        self.source.created_by = self.user
        self.source.save(update_fields=["created_by"])

        tree = GateModel.build_tree(file_data_id=self.file_a.id)

        root = next(gate for gate in tree if gate["id"] == self.source.id)
        self.assertEqual(root["created_by"], self.user.id)
        self.assertEqual(root["created_by_name"], "Ana Souza")
        self.assertNotIn("created_by__username", root)

        child = root["children"][0]
        self.assertIsNone(child["created_by_name"])

    def test_patch_experiment_scope_requires_name_or_color(self):
        res = self._patch_gate(self.source, scope="experiment", plot_config={"a": 1})

        self.assertEqual(res.status_code, 400)

    def test_patch_experiment_scope_requires_permission(self):
        self.client.force_authenticate(self.outsider)

        res = self._patch_gate(self.source, name="CD4+", scope="experiment")

        self.assertEqual(res.status_code, 404)
        self.copy_b.refresh_from_db()
        self.assertEqual(self.copy_b.name, "P1")


class GateSubsampleScopeTestCase(GateFixtureMixin, TestCase):
    """BE-07 parte 2: `scope="subsample"` restringe o lote ao subsample."""

    def setUp(self):
        super().setUp()
        self.tempo_1 = SubsampleModel.objects.create(
            experiment=self.experiment, name="tempo_1", source_path="tempo_1"
        )
        self.tempo_2 = SubsampleModel.objects.create(
            experiment=self.experiment, name="tempo_2", source_path="tempo_2"
        )
        # a e b no mesmo subsample; c em outro.
        for file_data, subsample in (
            (self.file_a, self.tempo_1),
            (self.file_b, self.tempo_1),
            (self.file_c, self.tempo_2),
        ):
            file_data.subsample = subsample
            file_data.save(update_fields=["subsample"])

    def test_patch_subsample_scope_propagates_only_inside_subsample(self):
        res = self._patch_gate(self.source, name="CD4+", scope="subsample")

        self.assertEqual(res.status_code, 200)
        self.copy_b.refresh_from_db()
        self.copy_c.refresh_from_db()
        self.assertEqual(self.copy_b.name, "CD4+")
        self.assertEqual(self.copy_c.name, "P1")
        self.assertEqual(res.data["propagated_gate_ids"], [self.copy_b.id])
        self.assertEqual(res.data["applied_scope"], "subsample")

    def test_patch_subsample_scope_falls_back_to_file_without_subsample(self):
        self.file_a.subsample = None
        self.file_a.save(update_fields=["subsample"])

        res = self._patch_gate(self.source, name="CD4+", scope="subsample")

        self.assertEqual(res.status_code, 200)
        self.copy_b.refresh_from_db()
        self.assertEqual(self.copy_b.name, "P1")
        self.assertEqual(res.data["propagated_gate_ids"], [])
        self.assertEqual(res.data["applied_scope"], "file")

    def test_patch_subsample_scope_keeps_copy_attached_on_geometry(self):
        coords = {
            "type": "rectangle",
            "x_axis": "FSC-A",
            "y_axis": "SSC-A",
            "startX": 0,
            "endX": 1,
            "startY": 0,
            "endY": 1,
        }

        res = self._patch_gate(self.copy_b, gate_coordinates=coords, scope="subsample")

        self.assertEqual(res.status_code, 200)
        self.copy_b.refresh_from_db()
        self.copy_c.refresh_from_db()
        self.source.refresh_from_db()
        self.assertEqual(self.copy_b.copied_from_id, self.source.id)
        self.assertEqual(self.source.gate_coordinates, coords)
        self.assertNotEqual(self.copy_c.gate_coordinates, coords)

    def test_dry_run_lists_affected_samples_without_writing(self):
        res = self._patch_gate(
            self.source, name="CD4+", scope="subsample", dry_run=True
        )

        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data["dry_run"])
        self.assertEqual(
            [item["file_data_id"] for item in res.data["affected"]], [self.file_b.id]
        )
        self.source.refresh_from_db()
        self.copy_b.refresh_from_db()
        self.assertEqual(self.source.name, "P1")
        self.assertEqual(self.copy_b.name, "P1")

    def test_delete_batch_subsample_scope_keeps_other_subsamples(self):
        res = self._delete_batch(source_gate_ids=[self.source.id], scope="subsample")

        self.assertEqual(res.status_code, 200)
        self.assertFalse(GateModel.objects.filter(id=self.copy_b.id).exists())
        self.assertTrue(GateModel.objects.filter(id=self.copy_c.id).exists())
        self.assertTrue(GateModel.objects.filter(id=self.source.id).exists())

    def test_delete_batch_subsample_scope_without_subsample_hits_only_source(self):
        self.file_a.subsample = None
        self.file_a.save(update_fields=["subsample"])

        res = self._delete_batch(
            source_gate_ids=[self.source.id], scope="subsample", include_source=True
        )

        self.assertEqual(res.status_code, 200)
        self.assertFalse(GateModel.objects.filter(id=self.source.id).exists())
        self.assertTrue(GateModel.objects.filter(id=self.copy_b.id).exists())


class GatePermissionTestCase(GateFixtureMixin, TestCase):
    """ADR-0014: gates passam pelo queryset escopado (gates_visible_to).

    Anônimo recebe 401; quem está fora do experimento recebe 404 — nunca 200
    com dados alheios.
    """

    def test_anonimo_recebe_401_em_todos_os_endpoints(self):
        client = APIClient()
        checks = [
            (client.get, f"/analytics/gate/{self.source.id}/list"),
            (client.get, f"/analytics/gate/{self.source.id}/density"),
            (client.patch, f"/analytics/gate/{self.source.id}"),
            (client.delete, f"/analytics/gate/{self.source.id}"),
            (client.post, "/analytics/gate"),
            (client.post, "/analytics/gate/apply"),
            (client.post, "/analytics/gate/delete-batch"),
        ]
        for call, url in checks:
            with self.subTest(url=url):
                self.assertEqual(call(url).status_code, 401)

    def test_outsider_404_nas_leituras_de_gate(self):
        self.client.force_authenticate(self.outsider)
        urls = [
            f"/analytics/gate/{self.source.id}/list",
            f"/analytics/gate/{self.source.id}/density?x=FSC-A&y=SSC-A",
        ]
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 404)

    def test_outsider_404_nas_escritas_de_gate(self):
        self.client.force_authenticate(self.outsider)
        self.assertEqual(self._patch_gate(self.source, name="X").status_code, 404)
        self.assertEqual(
            self.client.delete(f"/analytics/gate/{self.source.id}").status_code,
            404,
        )
        self.assertEqual(
            self._delete_batch(source_gate_ids=[self.source.id]).status_code, 404
        )
        self.source.refresh_from_db()
        self.assertEqual(self.source.name, "P1")

    def test_outsider_nao_cria_gate_em_amostra_alheia(self):
        self.client.force_authenticate(self.outsider)
        res = self.client.post(
            "/analytics/gate",
            {
                "name": "P1",
                "gate_coordinates": {},
                "file_data": self.file_a.id,
                "dashboard": {
                    "name": "dash",
                    "dashboard_config": {},
                    "file_data": self.file_a.id,
                },
            },
            format="json",
        )

        self.assertEqual(res.status_code, 403)

    def test_outsider_nao_aplica_gate_proprio_em_amostra_alheia(self):
        own_exp = ExperimentModel.objects.create(
            title="dele", type="t", created_by=self.outsider
        )
        own_gate = self._gate(self._file(own_exp, "x.fcs"), "P1")
        self.client.force_authenticate(self.outsider)

        res = self.client.post(
            "/analytics/gate/apply",
            {
                "source_gate_ids": [own_gate.id],
                "target_file_data_ids": [self.file_b.id],
            },
            format="json",
        )

        self.assertEqual(res.status_code, 404)

    def test_dono_segue_editando_o_gate(self):
        res = self._patch_gate(self.source, name="P1-renomeado")

        self.assertEqual(res.status_code, 200)
        self.source.refresh_from_db()
        self.assertEqual(self.source.name, "P1-renomeado")


class GateMissingChannelTestCase(GateFixtureMixin, TestCase):
    """BE-18/ADR-0016: gate que referencia canal ausente na amostra fica
    não-avaliável — a linhagem abaixo corta junto, sem stat fictícia."""

    def setUp(self):
        super().setUp()
        # file_a tem painel completo; file_b foi adquirido sem FITC-A/APC-A.
        self._set_events(
            self.file_a,
            [
                {"FSC-A": 1.0, "SSC-A": 2.0, "FITC-A": 3.0, "APC-A": 4.0},
                {"FSC-A": 5.0, "SSC-A": 6.0, "FITC-A": 7.0, "APC-A": 8.0},
            ],
        )
        self._set_events(
            self.file_b,
            [
                {"FSC-A": 1.0, "SSC-A": 2.0},
                {"FSC-A": 5.0, "SSC-A": 6.0},
            ],
        )

    def _set_events(self, file_data, records):
        file_data.data_set = records
        file_data.save(update_fields=["data_set"])

    def _gate_on_axes(self, file_data, name, x="FITC-A", y="APC-A", **kwargs):
        gate = self._gate(file_data, name, **kwargs)
        gate.dashboard.dashboard_config = {"x_axis_label": x, "y_axis_label": y}
        gate.dashboard.save(update_fields=["dashboard_config"])
        gate.gate_coordinates = {
            "type": "rectangle",
            "startX": 0,
            "endX": 10,
            "startY": 0,
            "endY": 10,
        }
        gate.save(update_fields=["gate_coordinates"])
        return gate

    def test_density_400_nomeia_canal_ausente_do_gate(self):
        gate = self._gate_on_axes(self.file_b, "FITC+")

        res = self.client.get(f"/analytics/gate/{gate.id}/density?x=FSC-A&y=SSC-A")

        self.assertEqual(res.status_code, 400)
        body = res.json()
        self.assertIn("FITC-A", body["detail"])
        self.assertEqual(sorted(body["missing_channels"]), ["APC-A", "FITC-A"])
        self.assertEqual(body["gate_id"], gate.id)

    def test_density_400_quando_eixo_pedido_nao_existe_na_amostra(self):
        gate = self._gate_on_axes(self.file_b, "P", x="FSC-A", y="SSC-A")

        res = self.client.get(f"/analytics/gate/{gate.id}/density?x=FITC-A&y=APC-A")

        self.assertEqual(res.status_code, 400)
        body = res.json()
        self.assertIn("FITC-A", body["detail"])
        self.assertEqual(sorted(body["missing_channels"]), ["APC-A", "FITC-A"])

    def test_density_200_vazio_quando_gate_valido_filtra_tudo(self):
        gate = self._gate_on_axes(self.file_b, "Vazio", x="FSC-A", y="SSC-A")
        gate.gate_coordinates = {
            "type": "rectangle",
            "startX": 1000,
            "endX": 2000,
            "startY": 1000,
            "endY": 2000,
        }
        gate.save(update_fields=["gate_coordinates"])

        res = self.client.get(f"/analytics/gate/{gate.id}/density?x=FSC-A&y=SSC-A")

        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["total_events"], 0)
        self.assertEqual(body["histogram"], [])

    def test_list_400_nomeia_canal_ausente_do_gate(self):
        gate = self._gate_on_axes(self.file_b, "FITC+")

        res = self.client.get(f"/analytics/gate/{gate.id}/list")

        self.assertEqual(res.status_code, 400)
        self.assertIn("FITC-A", res.json()["detail"])

    def test_recalculo_marca_gate_e_corta_a_linhagem(self):
        gate = self._gate_on_axes(self.file_b, "FITC+")
        child = self._gate_on_axes(
            self.file_b, "Sub", x="FSC-A", y="SSC-A", parent=gate
        )

        recalculate_gate_analysis(gate.id)

        gate.refresh_from_db()
        result = gate.analysis_result.analysis_result
        self.assertFalse(result["applicable"])
        self.assertEqual(result["reason"], "missing_channels")
        self.assertEqual(sorted(result["missing_channels"]), ["APC-A", "FITC-A"])
        self.assertEqual(result["blocked_by_gate"]["id"], gate.id)
        self.assertNotIn("summary_metrics", result)

        # O filho é avaliável sozinho, mas herda o corte do ancestral.
        child.refresh_from_db()
        child_result = child.analysis_result.analysis_result
        self.assertFalse(child_result["applicable"])
        self.assertEqual(child_result["blocked_by_gate"]["id"], gate.id)

    def test_recalculo_normal_quando_canais_existem(self):
        gate = self._gate_on_axes(self.file_a, "FITC+")

        recalculate_gate_analysis(gate.id)

        gate.refresh_from_db()
        result = gate.analysis_result.analysis_result
        self.assertNotEqual(result.get("applicable"), False)
        self.assertIn("summary_metrics", result)

    def test_apply_dry_run_avisa_amostra_sem_canal(self):
        source = self._gate_on_axes(self.file_a, "FITC+")

        res = self.client.post(
            "/analytics/gate/apply",
            {
                "source_gate_ids": [source.id],
                "target_file_data_ids": [self.file_b.id],
                "dry_run": True,
            },
            format="json",
        )

        self.assertEqual(res.status_code, 200)
        non_evaluable = res.json()["non_evaluable"]
        self.assertEqual(len(non_evaluable), 1)
        self.assertEqual(non_evaluable[0]["file_data_id"], self.file_b.id)
        self.assertEqual(non_evaluable[0]["missing_channels"], ["APC-A", "FITC-A"])
        self.assertIn(source.id, non_evaluable[0]["gate_ids"])
        self.assertFalse(
            GateModel.objects.filter(file_data=self.file_b, copied_from=source).exists()
        )

    def test_apply_cria_copia_marcada_nao_avaliavel(self):
        source = self._gate_on_axes(self.file_a, "FITC+")

        res = self.client.post(
            "/analytics/gate/apply",
            {
                "source_gate_ids": [source.id],
                "target_file_data_ids": [self.file_b.id],
            },
            format="json",
        )

        self.assertEqual(res.status_code, 201)
        self.assertEqual(len(res.json()["non_evaluable"]), 1)

        copy = GateModel.objects.get(file_data=self.file_b, copied_from=source)
        self.assertFalse(copy.analysis_result.analysis_result["applicable"])

    def test_apply_materializa_stats_no_gate_propagado(self):
        # Regressão card #90: o apply propagava o gate mas a amostra alvo
        # ficava sem analysis_result quando get_dataframe não resolvia o
        # ZIP do upload — a figura (BE-33) reportava a população como
        # unmatched. Aqui a amostra alvo tem eventos e o gate deve sair
        # com stats calculadas.
        self._set_events(
            self.file_b,
            [
                {"FSC-A": 1.0, "SSC-A": 2.0},
                {"FSC-A": 5.0, "SSC-A": 6.0},
            ],
        )
        self._set_events(
            self.file_c,
            [{"FSC-A": 3.0, "SSC-A": 4.0}],
        )
        source = self._gate_on_axes(self.file_a, "P-stats", x="FSC-A", y="SSC-A")

        res = self.client.post(
            "/analytics/gate/apply",
            {
                "source_gate_ids": [source.id],
                "target_file_data_ids": [self.file_b.id, self.file_c.id],
            },
            format="json",
        )

        self.assertEqual(res.status_code, 201)
        for file_data, expected_count in ((self.file_b, 2), (self.file_c, 1)):
            copy = GateModel.objects.get(file_data=file_data, copied_from=source)
            result = copy.analysis_result.analysis_result
            self.assertEqual(result["summary_metrics"]["count"], expected_count)
            self.assertIn("fsc_a", result["channel_statistics"])


class AnalysisHistoryTestCase(GateFixtureMixin, TestCase):
    """BE-08: log append-only de mutações + reversão com dry_run/conflitos."""

    def _history(self, **params):
        return self.client.get(
            f"/analytics/experiment/{self.experiment.id}/history/", params
        )

    def test_criar_gate_registra_revisao(self):
        res = self.client.post(
            "/analytics/gate",
            {
                "name": "novo",
                "file_data": self.file_a.id,
                "gate_coordinates": {},
                "dashboard": {
                    "name": "dash-novo",
                    "dashboard_config": {},
                    "file_data": self.file_a.id,
                },
            },
            format="json",
        )

        self.assertEqual(res.status_code, 201, res.data)
        res = self._history()
        self.assertEqual(res.status_code, 200)
        self.assertTrue(any(r["action"] == "create" for r in res.data["results"]))

    def test_rename_grava_antes_depois_e_reverte(self):
        res = self._patch_gate(self.source, name="CD4+")

        self.assertEqual(res.status_code, 200)
        res = self._history(target=f"gate:{self.source.id}")
        revision = res.data["results"][0]
        self.assertEqual(revision["action"], "rename")
        self.assertTrue(revision["revertible"])

        detail = self.client.get(f"/analytics/history/{revision['id']}/")
        self.assertEqual(
            detail.data["payload_before"]["gates"][str(self.source.id)]["name"],
            "P1",
        )
        self.assertEqual(
            detail.data["payload_after"]["gates"][str(self.source.id)]["name"],
            "CD4+",
        )

        # Reverte de verdade: o nome volta e nasce uma revisão "revert".
        res = self.client.post(f"/analytics/history/{revision['id']}/revert/", {})
        self.assertEqual(res.status_code, 200)
        self.source.refresh_from_db()
        self.assertEqual(self.source.name, "P1")
        res = self._history()
        self.assertTrue(any(r["action"] == "revert" for r in res.data["results"]))

    def test_rename_propagado_registra_copias_e_reverte_todas(self):
        res = self._patch_gate(self.source, name="CD4+", scope="experiment")

        self.assertEqual(res.status_code, 200)
        revision = self._history().data["results"][0]
        self.assertEqual(
            set(revision["affected_ids"]),
            {self.source.id, self.copy_b.id, self.copy_c.id},
        )

        self.client.post(f"/analytics/history/{revision['id']}/revert/", {})
        for gate in (self.source, self.copy_b, self.copy_c):
            gate.refresh_from_db()
            self.assertEqual(gate.name, "P1")

    def test_revert_bloqueia_quando_gate_mudou_depois(self):
        self._patch_gate(self.source, name="CD4+")
        revision = self._history().data["results"][0]
        self._patch_gate(self.source, name="CD8+")

        res = self.client.post(f"/analytics/history/{revision['id']}/revert/", {})

        self.assertEqual(res.status_code, 409)
        self.source.refresh_from_db()
        self.assertEqual(self.source.name, "CD8+")

    def test_delete_batch_registra_subarvore_e_revert_recria(self):
        res = self._delete_batch(source_gate_ids=[self.source.id], recursive=True)
        self.assertEqual(res.status_code, 200)
        self.assertFalse(GateModel.objects.filter(id=self.child.id).exists())

        revision = self._history().data["results"][0]
        self.assertEqual(revision["action"], "delete")

        res = self.client.post(f"/analytics/history/{revision['id']}/revert/", {})
        self.assertEqual(res.status_code, 200)
        recreated = GateModel.objects.filter(file_data=self.file_a, name="P1").first()
        self.assertIsNotNone(recreated)
        self.assertTrue(GateModel.objects.filter(parent=recreated, name="P2").exists())

    def test_dry_run_nao_grava_nada(self):
        self._patch_gate(self.source, name="CD4+")
        revision = self._history().data["results"][0]
        count = self._history().data["results"].__len__()

        res = self.client.post(
            f"/analytics/history/{revision['id']}/revert/", {"dry_run": True}
        )

        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data["would_change"])
        self.source.refresh_from_db()
        self.assertEqual(self.source.name, "CD4+")
        self.assertEqual(len(self._history().data["results"]), count)

    def test_disable_enable_amostra_registra_e_reverte(self):
        res = self.client.post(f"/experiment/file/{self.file_a.id}/disable")
        self.assertEqual(res.status_code, 200)

        revision = self._history().data["results"][0]
        self.assertEqual(revision["action"], "disable")
        self.assertEqual(revision["target"]["type"], "file")

        res = self.client.post(f"/analytics/history/{revision['id']}/revert/", {})
        self.assertEqual(res.status_code, 200)
        self.file_a.refresh_from_db()
        self.assertTrue(self.file_a.active)

    def test_mover_subsample_registra_e_reverte(self):
        subsample = SubsampleModel.objects.create(
            experiment=self.experiment, name="tempo_1"
        )
        res = self.client.patch(
            f"/experiment/file/{self.file_a.id}/subsample",
            {"subsample": subsample.id},
            format="json",
        )
        self.assertEqual(res.status_code, 200)

        revision = self._history().data["results"][0]
        self.assertEqual(revision["action"], "move_subsample")

        res = self.client.post(f"/analytics/history/{revision['id']}/revert/", {})
        self.assertEqual(res.status_code, 200)
        self.file_a.refresh_from_db()
        self.assertIsNone(self.file_a.subsample_id)

    def test_outsider_nao_le_historico_nem_reverte(self):
        self._patch_gate(self.source, name="CD4+")
        self.client.force_authenticate(self.outsider)

        res = self._history()
        self.assertEqual(res.status_code, 404)

        revision_id = AnalysisRevision.objects.first().id
        res = self.client.post(f"/analytics/history/{revision_id}/revert/", {})
        self.assertEqual(res.status_code, 404)

    def test_cursor_pagina_resultados(self):
        for i in range(3):
            self._patch_gate(self.source, name=f"P{i}")

        page1 = self._history()
        self.assertEqual(page1.status_code, 200)
        results = page1.data["results"]
        self.assertEqual(len(results), 3)
        cursor = page1.data["results"][-1]["id"]

        page2 = self._history(cursor=cursor)
        self.assertEqual(page2.status_code, 200)


class AnalysisCheckpointTestCase(GateFixtureMixin, TestCase):
    """BE-20: checkpoints nomeados, sessões derivadas e restore em cadeia."""

    def _checkpoints_url(self):
        return f"/analytics/experiment/{self.experiment.id}/checkpoints/"

    def _restore_url(self, revision_id):
        return (
            f"/analytics/experiment/{self.experiment.id}"
            f"/history/{revision_id}/restore/"
        )

    def test_criar_checkpoint_marca_ultima_revisao(self):
        self._patch_gate(self.source, name="CD4+")
        res = self.client.post(
            self._checkpoints_url(),
            {"message": "antes do reprocessamento"},
            format="json",
        )
        self.assertEqual(res.status_code, 201)
        last = AnalysisRevision.objects.order_by("-id").first()
        self.assertEqual(res.data["revision"], last.id)
        self.assertEqual(res.data["message"], "antes do reprocessamento")

    def test_pin_com_revision_id_e_rename_delete(self):
        self._patch_gate(self.source, name="CD4+")
        rev = AnalysisRevision.objects.order_by("-id").first()
        res = self.client.post(
            self._checkpoints_url(), {"revision_id": rev.id}, format="json"
        )
        self.assertEqual(res.status_code, 201)
        cp_id = res.data["id"]

        res = self.client.patch(
            f"/analytics/checkpoints/{cp_id}/",
            {"message": "ponto X"},
            format="json",
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["message"], "ponto X")

        res = self.client.delete(f"/analytics/checkpoints/{cp_id}/")
        self.assertEqual(res.status_code, 204)
        self.assertFalse(
            self._checkpoints_list().filter(pk=cp_id, active=True).exists()
        )

    def _checkpoints_list(self):
        from analytics.models import AnalysisCheckpoint

        return AnalysisCheckpoint.objects.all()

    def test_restore_desfaz_cadeia_e_recria_delete(self):
        self._patch_gate(self.source, name="CD4+")
        base = AnalysisRevision.objects.order_by("-id").first()

        self._patch_gate(self.source, name="CD8+")
        self._delete_batch(source_gate_ids=[self.source.id], recursive=True)
        self.assertFalse(GateModel.objects.filter(id=self.child.id).exists())

        res = self.client.post(self._restore_url(base.id), {})
        self.assertEqual(res.status_code, 200)

        recreated = GateModel.objects.filter(file_data=self.file_a, name="CD4+").first()
        self.assertIsNotNone(recreated)
        self.assertTrue(GateModel.objects.filter(parent=recreated, name="P2").exists())
        restore_rev = AnalysisRevision.objects.order_by("-id").first()
        self.assertEqual(restore_rev.action, "restore")
        self.assertEqual(restore_rev.reverts_id, base.id)

    def test_restore_dry_run_nao_grava_nada(self):
        self._patch_gate(self.source, name="CD4+")
        base = AnalysisRevision.objects.order_by("-id").first()
        self._patch_gate(self.source, name="CD8+")
        count = AnalysisRevision.objects.count()

        res = self.client.post(self._restore_url(base.id), {"dry_run": True})

        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data["would_change"])
        self.source.refresh_from_db()
        self.assertEqual(self.source.name, "CD8+")
        self.assertEqual(AnalysisRevision.objects.count(), count)

    def test_restore_conflito_409_e_force_sobrescreve(self):
        self._patch_gate(self.source, name="CD4+")
        base = AnalysisRevision.objects.order_by("-id").first()
        self._patch_gate(self.source, name="CD8+")
        # drift fora da cadeia: a revisão do revert abaixo aponta para o meio
        mid_rev = AnalysisRevision.objects.order_by("-id").first()
        # simula drift: muda o gate direto no banco, sem revisão
        GateModel.objects.filter(pk=self.source.id).update(name="HACK")

        res = self.client.post(self._restore_url(base.id), {})
        self.assertEqual(res.status_code, 409)

        res = self.client.post(self._restore_url(base.id), {"force": True})
        self.assertEqual(res.status_code, 200)
        self.source.refresh_from_db()
        self.assertEqual(self.source.name, "CD4+")
        self.assertIsNotNone(mid_rev)

    def test_history_agrupado_em_sessoes(self):
        self._patch_gate(self.source, name="CD4+")
        old = AnalysisRevision.objects.order_by("-id").first()
        # empurra a revisão para trás no tempo para abrir gap de sessão
        from datetime import timedelta as td

        from django.utils import timezone

        AnalysisRevision.objects.filter(pk=old.id).update(
            created_at=timezone.now() - td(hours=2)
        )
        self._patch_gate(self.source, name="CD8+")

        res = self.client.get(
            f"/analytics/experiment/{self.experiment.id}/history/?grouped=1"
        )
        self.assertEqual(res.status_code, 200)
        sessions = res.data["sessions"]
        self.assertEqual(len(sessions), 2)
        self.assertEqual(sessions[0]["count"], 1)
        self.assertEqual(sessions[0]["revisions"][0]["action"], "rename")

    def test_state_preview_reconstrói_arvore(self):
        self._patch_gate(self.source, name="CD4+")
        base = AnalysisRevision.objects.order_by("-id").first()
        self._patch_gate(self.source, name="CD8+")
        self._delete_batch(source_gate_ids=[self.source.id], recursive=True)

        res = self.client.get(f"/analytics/history/{base.id}/state/")
        self.assertEqual(res.status_code, 200)
        gates = res.data["files"][str(self.file_a.id)]
        by_name = {g["name"] for g in gates}
        self.assertIn("CD4+", by_name)
        self.assertIn("P2", by_name)

    def test_outsider_nao_cria_nem_restaura(self):
        self._patch_gate(self.source, name="CD4+")
        rev = AnalysisRevision.objects.order_by("-id").first()
        self.client.force_authenticate(self.outsider)

        res = self.client.post(self._checkpoints_url(), {}, format="json")
        self.assertEqual(res.status_code, 404)
        res = self.client.post(self._restore_url(rev.id), {})
        self.assertEqual(res.status_code, 404)

    def test_reverter_restore_refaz_o_desfeito(self):
        self._patch_gate(self.source, name="CD4+")
        base = AnalysisRevision.objects.order_by("-id").first()
        self._patch_gate(self.source, name="CD8+")

        res = self.client.post(self._restore_url(base.id), {})
        self.assertEqual(res.status_code, 200)
        self.source.refresh_from_db()
        self.assertEqual(self.source.name, "CD4+")

        restore_rev = AnalysisRevision.objects.order_by("-id").first()
        res = self.client.post(f"/analytics/history/{restore_rev.id}/revert/", {})
        self.assertEqual(res.status_code, 200)
        self.source.refresh_from_db()
        self.assertEqual(self.source.name, "CD8+")


class HistoryFileFilterTestCase(GateFixtureMixin, TestCase):
    """?file=<file_data_id> recorta a timeline pela amostra (FE-27)."""

    def _history(self, **params):
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"/analytics/experiment/{self.experiment.id}/history/"
        return self.client.get(f"{url}?{qs}" if qs else url)

    def test_filtro_por_amostra(self):
        self._patch_gate(self.source, name="P1x")  # file_a
        self._patch_gate(self.copy_b, name="P1y")  # file_b
        from analytics.models import CompensationMatrix
        from fcs_parser.services.compensation import set_applied_compensation

        m = CompensationMatrix.objects.create(
            experiment=self.experiment,
            channels=["FITC-A"],
            matrix=[[1.0]],
            source="manual",
        )
        set_applied_compensation(self.experiment, m, self.user)  # exp-wide

        res = self._history(file=self.file_a.id)
        self.assertEqual(res.status_code, 200)
        actions = [(r["action"], r["file_data"]) for r in res.data["results"]]
        # gate do file_a + compensação (file_data None) entram no recorte
        self.assertIn(("rename", self.file_a.id), actions)
        self.assertIn(("compensation_apply", None), actions)
        # gate do file_b fica de fora
        self.assertNotIn(("rename", self.file_b.id), actions)

    def test_sem_filtro_devolve_tudo(self):
        self._patch_gate(self.source, name="P1x")
        self._patch_gate(self.copy_b, name="P1y")
        res = self._history()
        self.assertEqual(res.status_code, 200)
        self.assertGreaterEqual(len(res.data["results"]), 2)

    def test_gate_deletado_mantem_vinculo_a_amostra(self):
        gid = self.source.id
        res_del = self._delete_batch(
            source_gate_ids=[gid], scope="file", recursive=True
        )
        self.assertEqual(res_del.status_code, 200)
        res = self._history(file=self.file_a.id)
        deletes = [r for r in res.data["results"] if r["action"] == "delete"]
        self.assertTrue(deletes)
        self.assertEqual(deletes[0]["file_data"], self.file_a.id)


class BranchWorkflowTestCase(GateFixtureMixin, TestCase):
    """BE-23/ADR-0020: branches como cópias materializadas + merge."""

    def setUp(self):
        super().setUp()
        from analytics.services.branches import ensure_main_branch

        self.ensure_main_branch = ensure_main_branch
        self.main = ensure_main_branch(self.experiment)

    def _branches_url(self):
        return f"/analytics/experiment/{self.experiment.id}/branches/"

    def _fork(self, name="revisao", base=None):
        payload = {"name": name}
        if base is not None:
            payload["base_branch_id"] = base.id
        return self.client.post(self._branches_url(), payload, format="json")

    def _merge(self, branch, **payload):
        return self.client.post(
            f"/analytics/branches/{branch.id}/merge/", payload, format="json"
        )

    # -- criação e isolamento ------------------------------------------------

    def test_gates_sem_branch_caem_na_main(self):
        self.source.refresh_from_db()
        self.assertEqual(self.source.branch_id, self.main.id)
        self.assertTrue(self.main.is_main)

    def test_lista_devolve_main(self):
        res = self.client.get(self._branches_url())

        self.assertEqual(res.status_code, 200)
        names = [b["name"] for b in res.data["results"]]
        self.assertIn("main", names)

    def test_fork_materializa_arvore_com_forked_from(self):
        res = self._fork("revisao")

        self.assertEqual(res.status_code, 201)
        branch = AnalysisBranch.objects.get(id=res.data["id"])
        self.assertEqual(branch.base_branch_id, self.main.id)

        b_source = GateModel.objects.get(
            branch=branch, file_data=self.file_a, name="P1"
        )
        self.assertEqual(b_source.forked_from_id, self.source.id)
        self.assertIsNone(b_source.copied_from_id)
        self.assertTrue(
            GateModel.objects.filter(
                branch=branch, file_data=self.file_a, name="P2", parent=b_source
            ).exists()
        )
        # a main não ganhou nem perdeu gates
        self.assertEqual(
            GateModel.objects.filter(branch=self.main).count(),
            GateModel.objects.filter(branch=branch).count(),
        )
        self.assertTrue(
            AnalysisRevision.objects.filter(
                action="fork", branch=branch, target_id=branch.id
            ).exists()
        )

    def test_edicao_na_branch_nao_toca_a_main(self):
        branch = AnalysisBranch.objects.get(id=self._fork("revisao").data["id"])
        b_gate = GateModel.objects.get(branch=branch, file_data=self.file_a, name="P1")

        res = self._patch_gate(b_gate, name="P1-orientador")

        self.assertEqual(res.status_code, 200)
        self.source.refresh_from_db()
        self.assertEqual(self.source.name, "P1")

    def test_propagacao_nao_cruza_branches(self):
        branch = AnalysisBranch.objects.get(id=self._fork("revisao").data["id"])
        b_src = GateModel.objects.get(branch=branch, file_data=self.file_a, name="P1")
        b_copy = GateModel.objects.get(branch=branch, file_data=self.file_b, name="P1")
        b_copy.copied_from = b_src
        b_copy.save(update_fields=["copied_from"])

        res = self._patch_gate(b_src, name="CD4+", scope="experiment")

        self.assertEqual(res.status_code, 200)
        self.copy_b.refresh_from_db()
        b_copy.refresh_from_db()
        self.assertEqual(self.copy_b.name, "P1")  # main intacta
        self.assertEqual(b_copy.name, "CD4+")  # propagou só na branch

    def test_nome_duplicado_e_base_invalida(self):
        self.assertEqual(self._fork("revisao").status_code, 201)
        self.assertEqual(self._fork("revisao").status_code, 409)
        # base de outro experimento não é base válida
        outra = self.ensure_main_branch(self.other_experiment)
        self.assertEqual(self._fork("x", base=outra).status_code, 404)

    def test_fork_exige_permissao(self):
        self.client.force_authenticate(self.outsider)
        self.assertEqual(self._fork("revisao").status_code, 404)

    def test_main_nao_arquiva_nem_renomeia(self):
        res = self.client.patch(
            f"/analytics/branches/{self.main.id}/", {"name": "x"}, format="json"
        )
        self.assertEqual(res.status_code, 400)
        res = self.client.delete(f"/analytics/branches/{self.main.id}/")
        self.assertEqual(res.status_code, 400)

    def test_arquivar_branch_e_soft_delete(self):
        branch = AnalysisBranch.objects.get(id=self._fork("revisao").data["id"])

        res = self.client.delete(f"/analytics/branches/{branch.id}/")

        self.assertEqual(res.status_code, 204)
        branch.refresh_from_db()
        self.assertFalse(branch.active)
        # gates da branch continuam no banco (soft delete)
        self.assertTrue(GateModel.objects.filter(branch=branch).exists())

    # -- diff -----------------------------------------------------------------

    def _gate_on(self, branch, file_data, name, parent=None, **kw):
        return GateModel.objects.create(
            file_data=file_data,
            name=name,
            dashboard=DashboardModel.objects.create(
                name=f"{file_data.file_name}-{name}", file_data=file_data
            ),
            parent=parent,
            branch=branch,
            **kw,
        )

    def test_diff_adicao(self):
        branch = AnalysisBranch.objects.get(id=self._fork("revisao").data["id"])
        self._gate_on(branch, self.file_a, "P9")

        res = self.client.get(f"/analytics/branches/{branch.id}/diff/")

        self.assertEqual(res.status_code, 200)
        creates = [c for c in res.data["changes"] if c["type"] == "create"]
        self.assertEqual(len(creates), 1)
        self.assertEqual(creates[0]["name"], "P9")
        self.assertEqual(res.data["conflicts"], [])

    def test_diff_modificacao_so_na_branch(self):
        branch = AnalysisBranch.objects.get(id=self._fork("revisao").data["id"])
        b_gate = GateModel.objects.get(branch=branch, file_data=self.file_a, name="P1")
        b_gate.color = "#ff0000"
        b_gate.save(update_fields=["color"])

        res = self.client.get(f"/analytics/branches/{branch.id}/diff/")

        updates = [c for c in res.data["changes"] if c["type"] == "update"]
        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0]["fields"], {"color": "#ff0000"})
        self.assertEqual(res.data["conflicts"], [])

    def test_diff_remocao_na_branch(self):
        branch = AnalysisBranch.objects.get(id=self._fork("revisao").data["id"])
        GateModel.objects.get(branch=branch, file_data=self.file_c, name="P1").delete()

        res = self.client.get(f"/analytics/branches/{branch.id}/diff/")

        deletes = [c for c in res.data["changes"] if c["type"] == "delete"]
        self.assertEqual(len(deletes), 1)
        self.assertEqual(deletes[0]["target_gate_id"], self.copy_c.id)

    def test_diff_edit_dos_dois_lados_vira_conflito(self):
        branch = AnalysisBranch.objects.get(id=self._fork("revisao").data["id"])
        b_gate = GateModel.objects.get(branch=branch, file_data=self.file_a, name="P1")
        b_gate.color = "#ff0000"
        b_gate.save(update_fields=["color"])
        self.source.color = "#00ff00"
        self.source.save(update_fields=["color"])

        res = self.client.get(f"/analytics/branches/{branch.id}/diff/")

        self.assertEqual(len(res.data["conflicts"]), 1)
        conflict = res.data["conflicts"][0]
        self.assertEqual(conflict["key"], f"f:{self.source.id}")
        self.assertEqual(conflict["type"], "modified_both")

    def test_diff_edit_na_branch_delete_na_base(self):
        branch = AnalysisBranch.objects.get(id=self._fork("revisao").data["id"])
        b_gate = GateModel.objects.get(branch=branch, file_data=self.file_c, name="P1")
        b_gate.color = "#ff0000"
        b_gate.save(update_fields=["color"])
        self.copy_c.delete()  # apagado na main

        res = self.client.get(f"/analytics/branches/{branch.id}/diff/")

        self.assertEqual(len(res.data["conflicts"]), 1)
        self.assertEqual(res.data["conflicts"][0]["type"], "deleted_in_target")

    # -- merge -----------------------------------------------------------------

    def test_merge_dry_run_nao_escreve(self):
        branch = AnalysisBranch.objects.get(id=self._fork("revisao").data["id"])
        self._gate_on(branch, self.file_a, "P9")

        res = self._merge(branch, dry_run=True)

        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.data["changes"]), 1)
        self.assertFalse(
            GateModel.objects.filter(
                branch=self.main, file_data=self.file_a, name="P9"
            ).exists()
        )

    def test_merge_limpo_aplica_mudancas(self):
        branch = AnalysisBranch.objects.get(id=self._fork("revisao").data["id"])
        novo = self._gate_on(branch, self.file_a, "P9")
        b_gate = GateModel.objects.get(branch=branch, file_data=self.file_a, name="P1")
        b_gate.color = "#ff0000"
        b_gate.save(update_fields=["color"])
        GateModel.objects.get(branch=branch, file_data=self.file_c, name="P1").delete()

        res = self._merge(branch)

        self.assertEqual(res.status_code, 200, res.data)
        self.assertTrue(res.data["merged"])
        self.source.refresh_from_db()
        self.assertEqual(self.source.color, "#ff0000")
        self.assertTrue(
            GateModel.objects.filter(
                branch=self.main, file_data=self.file_a, name="P9"
            ).exists()
        )
        self.assertFalse(GateModel.objects.filter(id=self.copy_c.id).exists())
        merge_rev = AnalysisRevision.objects.get(
            id=res.data["merge_revision_id"], action="merge"
        )
        self.assertEqual(merge_rev.branch_id, self.main.id)
        self.assertTrue(merge_rev.payload_after["revision_ids"])

    def test_merge_com_conflito_sem_resolucao_da_409(self):
        branch = AnalysisBranch.objects.get(id=self._fork("revisao").data["id"])
        b_gate = GateModel.objects.get(branch=branch, file_data=self.file_a, name="P1")
        b_gate.color = "#ff0000"
        b_gate.save(update_fields=["color"])
        self.source.color = "#00ff00"
        self.source.save(update_fields=["color"])

        res = self._merge(branch)

        self.assertEqual(res.status_code, 409)
        self.source.refresh_from_db()
        self.assertEqual(self.source.color, "#00ff00")  # nada gravado

    def test_merge_theirs_e_mine(self):
        branch = AnalysisBranch.objects.get(id=self._fork("revisao").data["id"])
        b_gate = GateModel.objects.get(branch=branch, file_data=self.file_a, name="P1")
        b_gate.color = "#ff0000"
        b_gate.save(update_fields=["color"])
        self.source.color = "#00ff00"
        self.source.save(update_fields=["color"])
        key = f"f:{self.source.id}"

        res = self._merge(branch, resolutions={key: "mine"})
        self.assertEqual(res.status_code, 200)
        self.source.refresh_from_db()
        self.assertEqual(self.source.color, "#00ff00")

        branch2 = AnalysisBranch.objects.get(id=self._fork("tentativa2").data["id"])
        b2 = GateModel.objects.get(branch=branch2, file_data=self.file_a, name="P1")
        b2.color = "#0000ff"
        b2.save(update_fields=["color"])
        self.source.color = "#aaaaaa"
        self.source.save(update_fields=["color"])

        res = self._merge(branch2, resolutions={f"f:{self.source.id}": "theirs"})
        self.assertEqual(res.status_code, 200)
        self.source.refresh_from_db()
        self.assertEqual(self.source.color, "#0000ff")

    def test_merge_both_preserva_os_dois(self):
        branch = AnalysisBranch.objects.get(id=self._fork("revisao").data["id"])
        b_gate = GateModel.objects.get(branch=branch, file_data=self.file_a, name="P1")
        b_gate.color = "#ff0000"
        b_gate.save(update_fields=["color"])
        self.source.color = "#00ff00"
        self.source.save(update_fields=["color"])

        res = self._merge(branch, resolutions={f"f:{self.source.id}": "both"})

        self.assertEqual(res.status_code, 200)
        self.source.refresh_from_db()
        self.assertEqual(self.source.color, "#00ff00")  # mine preservado
        duplicata = GateModel.objects.filter(
            branch=self.main, file_data=self.file_a
        ).exclude(id__in=[self.source.id, self.child.id])
        self.assertEqual(duplicata.count(), 1)
        self.assertEqual(duplicata[0].name, "P1 (2)")
        self.assertEqual(duplicata[0].color, "#ff0000")

    def test_merge_reverte_em_cadeia(self):
        branch = AnalysisBranch.objects.get(id=self._fork("revisao").data["id"])
        self._gate_on(branch, self.file_a, "P9")
        b_gate = GateModel.objects.get(branch=branch, file_data=self.file_a, name="P1")
        b_gate.color = "#ff0000"
        b_gate.save(update_fields=["color"])

        res = self._merge(branch)
        self.assertEqual(res.status_code, 200)

        rev = self.client.post(
            f"/analytics/history/{res.data['merge_revision_id']}/revert/",
            {},
            format="json",
        )
        self.assertEqual(rev.status_code, 200)
        self.source.refresh_from_db()
        self.assertEqual(self.source.color, "#111111")
        self.assertFalse(
            GateModel.objects.filter(
                branch=self.main, file_data=self.file_a, name="P9"
            ).exists()
        )

    # -- histórico por branch ---------------------------------------------------

    def test_history_filtra_por_branch(self):
        branch = AnalysisBranch.objects.get(id=self._fork("revisao").data["id"])
        b_gate = GateModel.objects.get(branch=branch, file_data=self.file_a, name="P1")
        self._patch_gate(b_gate, name="P1-b")
        self._patch_gate(self.source, name="P1-main")

        res = self.client.get(
            f"/analytics/experiment/{self.experiment.id}/history/?branch={branch.id}"
        )
        actions = [(r["action"], r["branch"]) for r in res.data["results"]]
        self.assertIn(("rename", branch.id), actions)
        self.assertNotIn(("rename", self.main.id), actions)

        res_main = self.client.get(
            f"/analytics/experiment/{self.experiment.id}/history/?branch={self.main.id}"
        )
        actions_main = [(r["action"], r["branch"]) for r in res_main.data["results"]]
        self.assertIn(("rename", self.main.id), actions_main)
        self.assertNotIn(("rename", branch.id), actions_main)


class StatsAuditTestCase(TestCase):
    """Auditoria de stats (dívida): eixos de gate_coordinates têm precedência
    sobre os labels do dashboard em qualquer tipo de gate, e as métricas por
    canal expõem `n`/`rcv` calculados só sobre valores finitos."""

    def _gate(self, coords, dash_config=None):
        return SimpleNamespace(
            gate_coordinates=coords,
            dashboard=SimpleNamespace(dashboard_config=dash_config),
        )

    def _df(self):
        return pd.DataFrame(
            {
                "fsc_a": [1.0, 100.0],
                "ssc_a": [1.0, 100.0],
                "fitc_a": [5.0, 5.0],
                "apc_a": [5.0, 5.0],
            }
        )

    def test_retangulo_prefere_eixos_das_coordenadas(self):
        from analytics.gate_filter import apply_gate_filter, gate_axis_channels

        # Pelos labels do dashboard (fsc/ssc) só 1 evento entraria no
        # retângulo; pelos eixos gravados nas coords (fitc/apc) entram os 2.
        gate = self._gate(
            {
                "type": "rectangle",
                "x_axis": "FITC-A",
                "y_axis": "APC-A",
                "startX": 0,
                "endX": 10,
                "startY": 0,
                "endY": 10,
            },
            {"x_axis_label": "FSC-A", "y_axis_label": "SSC-A"},
        )

        self.assertEqual(gate_axis_channels(gate), ("FITC-A", "APC-A"))
        self.assertEqual(len(apply_gate_filter(self._df(), gate)), 2)

    def test_poligono_prefere_eixos_das_coordenadas(self):
        from analytics.gate_filter import apply_gate_filter

        gate = self._gate(
            {
                "type": "polygon",
                "x_axis": "FITC-A",
                "y_axis": "APC-A",
                "vertices": [[0, 0], [10, 0], [10, 10], [0, 10]],
            },
            {"x_axis_label": "FSC-A", "y_axis_label": "SSC-A"},
        )

        self.assertEqual(len(apply_gate_filter(self._df(), gate)), 2)

    def test_gate_sem_eixos_nas_coords_cai_no_dashboard(self):
        from analytics.gate_filter import apply_gate_filter, gate_axis_channels

        gate = self._gate(
            {"type": "rectangle", "startX": 0, "endX": 10, "startY": 0, "endY": 10},
            {"x_axis_label": "FSC-A", "y_axis_label": "SSC-A"},
        )

        self.assertEqual(gate_axis_channels(gate), ("FSC-A", "SSC-A"))
        self.assertEqual(len(apply_gate_filter(self._df(), gate)), 1)

    def test_metrics_calcula_mean_median_cv_rcv_e_n(self):
        df = pd.DataFrame({"fitc_a": [10.0, 20.0, 30.0, 40.0, 50.0]})

        metrics = calculate_cytometry_metrics(df, 10, df, ["fitc_a"])

        self.assertEqual(metrics["summary_metrics"]["count"], 5)
        self.assertAlmostEqual(
            metrics["summary_metrics"]["percent_of_total_population"], 0.5
        )
        stat = metrics["channel_statistics"]["fitc_a"]
        self.assertEqual(stat["n"], 5)
        self.assertAlmostEqual(stat["mean_mfi"], 30.0)
        self.assertAlmostEqual(stat["median_mfi"], 30.0)
        # P16 = 16.4, P84 = 43.6 → rCV = 27.2 / (2 * 30) * 100 = 45.3333
        self.assertAlmostEqual(stat["rcv"], 45.3333, places=3)
        self.assertAlmostEqual(stat["cv"], stat["std_dev"] / 30.0 * 100)

    def test_metrics_ignora_valores_nao_finitos_mas_conta_eventos(self):
        df = pd.DataFrame(
            {
                "fitc_a": [100.0, 200.0, np.nan, np.inf],
                "fsc_a": [np.nan, np.nan, np.nan, np.nan],
            }
        )

        metrics = calculate_cytometry_metrics(df, 4, df, ["fitc_a", "fsc_a"])

        # Evento com NaN continua evento: count conta linhas; `n` conta
        # só os valores finitos usados na média/mediana do canal.
        self.assertEqual(metrics["summary_metrics"]["count"], 4)
        stat = metrics["channel_statistics"]["fitc_a"]
        self.assertEqual(stat["n"], 2)
        self.assertAlmostEqual(stat["mean_mfi"], 150.0)
        self.assertAlmostEqual(stat["median_mfi"], 150.0)
        # Canal todo não-finito não emite stat NaN (quebraria o JSON).
        self.assertNotIn("fsc_a", metrics["channel_statistics"])

    def test_rcv_permanece_definido_quando_cv_quebra(self):
        # Mediana/média negativas: comum em canal compensado. `cv` fica
        # negativo/sem sentido; `rcv` mede a dispersão relativa à mediana.
        df = pd.DataFrame({"bv_a": [-50.0, -40.0, -30.0, -20.0, -10.0]})

        metrics = calculate_cytometry_metrics(df, 5, df, ["bv_a"])

        stat = metrics["channel_statistics"]["bv_a"]
        self.assertLess(stat["cv"], 0)
        self.assertNotEqual(stat["rcv"], 0)
        self.assertAlmostEqual(abs(stat["rcv"]), 45.3333, places=3)


@override_settings(ANALYSIS_FIGURES_ENABLED=True)
class AnalysisFigureTestCase(GateFixtureMixin, TestCase):
    """BE-33 — figuras de análise persistidas: spec + cache + procedência.

    Fixture: file_a/b/c no experiment com gates P1 (raiz) e P1>P2 em a;
    cópias P1 em b/c; file_other_exp em experimento alheio.
    """

    def setUp(self):
        super().setUp()
        # Amostras "prontas" têm data materializado (L2 ou legado).
        for fd in (self.file_a, self.file_b, self.file_c):
            fd.data_set = [{"fsc_a": 1.0}]
            fd.save(update_fields=["data_set"])
        self._set_stats(self.source, {"count": 10})
        self._set_stats(self.child, {"count": 5})
        self._set_stats(self.copy_b, {"count": 20})
        self._set_stats(self.copy_c, {"count": 30})

    def _set_stats(self, gate, summary=None, channels=None):
        from analytics.models import AnalysisResult

        AnalysisResult.objects.update_or_create(
            gate=gate,
            defaults={
                "analysis_result": {
                    "summary_metrics": {
                        "count": (summary or {}).get("count", 0),
                        "percent_of_total_population": 0.5,
                        "percent_of_parent_population": 0.5,
                    },
                    "channel_statistics": channels
                    or {
                        "fitc_a": {
                            "median_mfi": 42.0,
                            "mean_mfi": 40.0,
                            "std_dev": 5.0,
                            "cv": 12.5,
                            "rcv": 30.0,
                        }
                    },
                }
            },
        )

    def _spec(self, **overrides):
        spec = {
            "groups": [
                {"name": "D0", "file_data_ids": [self.file_a.id]},
                {"name": "D7", "file_data_ids": [self.file_b.id, self.file_c.id]},
            ],
            "populations": ["P1"],
            "metric": "percent_parent",
        }
        spec.update(overrides)
        return spec

    def _create(self, name="fig", chart_type="stats_bar", spec=None, **extra):
        payload = {"name": name, "chart_type": chart_type, "spec": spec or self._spec()}
        payload.update(extra)
        return self.client.post(
            f"/analytics/experiment/{self.experiment.id}/figures/",
            payload,
            format="json",
        )

    # -- create / validação -------------------------------------------------

    def test_create_computa_cache_e_ancora_revisao(self):
        from analytics.history import record_revision
        from analytics.models import AnalysisFigure

        revision = record_revision(
            experiment=self.experiment,
            action=AnalysisRevision.ACTION_UPDATE_GEOMETRY,
            target_type=AnalysisRevision.TARGET_GATE,
            target_id=self.source.id,
            user=self.user,
            summary="criou gate",
        )
        res = self._create()

        self.assertEqual(res.status_code, 201)
        data = res.json()
        cache = data["result_cache"]
        self.assertEqual(len(cache["rows"]), 3)
        self.assertEqual(
            cache["resolved_inputs"]["file_data_ids"],
            sorted([self.file_a.id, self.file_b.id, self.file_c.id]),
        )
        self.assertEqual(
            sorted(cache["resolved_inputs"]["gate_ids"]),
            sorted([self.source.id, self.copy_b.id, self.copy_c.id]),
        )
        self.assertFalse(data["is_stale"])
        figure = AnalysisFigure.objects.get(id=data["id"])
        self.assertEqual(figure.result_revision_id, revision.id)

    def test_create_rejeita_file_data_de_outro_experimento(self):
        spec = self._spec(
            groups=[{"name": "x", "file_data_ids": [self.file_other_exp.id]}]
        )
        res = self._create(spec=spec)

        self.assertEqual(res.status_code, 400)

    def test_create_rejeita_metrica_de_canal_sem_channel(self):
        res = self._create(spec=self._spec(metric="median_mfi"))

        self.assertEqual(res.status_code, 400)
        self.assertIn("channel", res.json())

    def test_create_rejeita_nome_duplicado_409(self):
        self.assertEqual(self._create(name="fig1").status_code, 201)

        res = self._create(name="fig1")

        self.assertEqual(res.status_code, 409)

    # -- resolução de população ---------------------------------------------

    def test_populacao_ausente_fora_do_cache_nao_zero(self):
        # file_c só tem P1 — "P1 > P2" não resolve lá; resolve em a e b? b só tem P1 também.
        spec = self._spec(populations=["P1 > P2"])

        res = self._create(spec=spec)

        self.assertEqual(res.status_code, 201)
        rows = res.json()["result_cache"]["rows"]
        # só file_a tem o gate filho
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["file_data_id"], self.file_a.id)
        self.assertEqual(
            res.json()["result_cache"]["unmatched"]["populations"],
            ["P1 > P2"],
        )

    def test_metrica_de_canal_leria_do_channel_statistics(self):
        spec = self._spec(metric="median_mfi", channel="fitc_a")

        res = self._create(spec=spec)

        self.assertEqual(res.status_code, 201)
        rows = res.json()["result_cache"]["rows"]
        self.assertEqual({r["value"] for r in rows}, {42.0})

    # -- distribution --------------------------------------------------------

    def test_distribution_cache_so_metadados_resolvidos(self):
        spec = self._spec(populations=["P1 > P2"], channel="fitc_a")

        res = self._create(chart_type="distribution", spec=spec)

        self.assertEqual(res.status_code, 201)
        cache = res.json()["result_cache"]
        # Sem rows — as curvas vêm do densityService live; o cache é
        # fingerprint: pares população×amostra resolvidos + unmatched.
        self.assertEqual(cache["rows"], [])
        self.assertEqual(
            cache["resolved_pairs"],
            [{"population": "P1 > P2", "file_data_id": self.file_a.id}],
        )
        self.assertEqual(cache["resolved_inputs"]["gate_ids"], [self.child.id])
        self.assertEqual(cache["unmatched"]["populations"], ["P1 > P2"])

    def test_distribution_exige_uma_populacao_e_channel(self):
        res_sem_channel = self._create(
            chart_type="distribution", spec=self._spec(populations=["P1"])
        )
        self.assertEqual(res_sem_channel.status_code, 400)

        res_duas_pops = self._create(
            chart_type="distribution",
            spec=self._spec(populations=["P1", "P2"], channel="fitc_a"),
        )
        self.assertEqual(res_duas_pops.status_code, 400)

    # -- fingerprint / staleness --------------------------------------------

    def test_editar_gate_da_figura_marca_stale(self):
        from analytics.history import record_revision
        from analytics.services.figures import figure_is_stale
        from analytics.models import AnalysisFigure

        figure = AnalysisFigure.objects.get(id=self._create().json()["id"])
        self.assertFalse(figure_is_stale(figure))

        record_revision(
            experiment=self.experiment,
            action=AnalysisRevision.ACTION_UPDATE_GEOMETRY,
            target_type=AnalysisRevision.TARGET_GATE,
            target_id=self.copy_b.id,
            user=self.user,
            summary="editou",
        )

        figure.refresh_from_db()
        self.assertTrue(figure_is_stale(figure))

    def test_editar_gate_fora_da_figura_nao_marca_stale(self):
        from analytics.history import record_revision
        from analytics.services.figures import figure_is_stale
        from analytics.models import AnalysisFigure

        figure = AnalysisFigure.objects.get(id=self._create().json()["id"])
        outro_gate = self._gate(self.file_b, "QZ")

        record_revision(
            experiment=self.experiment,
            action=AnalysisRevision.ACTION_RENAME,
            target_type=AnalysisRevision.TARGET_GATE,
            target_id=outro_gate.id,
            user=self.user,
            summary="renomeou",
        )

        figure.refresh_from_db()
        self.assertFalse(figure_is_stale(figure))

    def test_acao_experiment_wide_marca_stale(self):
        from analytics.history import record_revision
        from analytics.models import AnalysisFigure

        figure_id = self._create().json()["id"]

        res = self.client.post(
            f"/analytics/experiment/{self.experiment.id}/figures/",
            {"name": "other", "chart_type": "stats_bar", "spec": self._spec()},
            format="json",
        )
        figure = AnalysisFigure.objects.get(id=figure_id)

        record_revision(
            experiment=self.experiment,
            action=AnalysisRevision.ACTION_COMPENSATION_APPLY,
            target_type=AnalysisRevision.TARGET_COMPENSATION,
            target_id=1,
            user=self.user,
            summary="aplicou comp",
        )

        detail = self.client.get(f"/analytics/figures/{figure.id}/").json()
        self.assertTrue(detail["is_stale"])

    def test_spec_editado_sem_recompute_marca_stale(self):
        figure_id = self._create().json()["id"]

        res = self.client.patch(
            f"/analytics/figures/{figure_id}/",
            {"spec": self._spec(populations=["P1 > P2"])},
            format="json",
        )

        self.assertEqual(res.status_code, 200)
        detail = self.client.get(f"/analytics/figures/{figure_id}/").json()
        self.assertTrue(detail["is_stale"])

    # -- recompute -----------------------------------------------------------

    def test_recompute_move_ancora_e_reporta_removed_since_last(self):
        from analytics.services.branches import ensure_main_branch
        from analytics.models import AnalysisFigure

        # Toda amostra do spec resolve "P1 > P2" no cômputo inicial.
        for parent_fd in (self.copy_b, self.copy_c):
            child = self._gate(parent_fd.file_data, "P2", parent=parent_fd)
            self._set_stats(child, {"count": 5})
        figure = AnalysisFigure.objects.get(
            id=self._create(spec=self._spec(populations=["P1", "P1 > P2"])).json()["id"]
        )
        assert not figure.result_cache["unmatched"]["populations"]

        # Renomear o filho em file_a quebra o caminho "P1 > P2" só nela.
        self.child.name = "Renomeado"
        self.child.save(update_fields=["name"])

        res = self.client.post(f"/analytics/figures/{figure.id}/recompute/")

        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["removed_since_last"]["populations"], ["P1 > P2"])
        self.assertFalse(data["figure"]["is_stale"])
        figure.refresh_from_db()
        self.assertEqual(figure.result_revision_id, data["figure"]["result_revision"])

    def test_amostra_processando_vai_para_meta_warnings(self):
        self.file_c.data_set = None
        self.file_c.parquet_path = None
        self.file_c.save(update_fields=["data_set"])

        res = self._create()

        self.assertEqual(res.status_code, 201)
        warnings = res.json()["result_cache"]["meta"].get("warnings") or []
        self.assertTrue(any("processando" in w for w in warnings))

    # -- published / locking -------------------------------------------------

    def test_published_bloqueia_patch_de_spec_e_recompute(self):
        figure_id = self._create().json()["id"]
        self.client.patch(
            f"/analytics/figures/{figure_id}/", {"published": True}, format="json"
        )

        res_spec = self.client.patch(
            f"/analytics/figures/{figure_id}/",
            {"spec": self._spec(populations=["P1"])},
            format="json",
        )
        res_recompute = self.client.post(f"/analytics/figures/{figure_id}/recompute/")

        self.assertEqual(res_spec.status_code, 409)
        self.assertEqual(res_recompute.status_code, 409)

    def test_patch_updated_at_divergente_412(self):
        detail = self.client.get(
            f"/analytics/figures/{self._create().json()['id']}/"
        ).json()

        res = self.client.patch(
            f"/analytics/figures/{detail['id']}/",
            {"name": "novo", "updated_at": "2000-01-01T00:00:00Z"},
            format="json",
        )

        self.assertEqual(res.status_code, 412)

    def test_patch_updated_at_igual_passa(self):
        detail = self.client.get(
            f"/analytics/figures/{self._create().json()['id']}/"
        ).json()

        res = self.client.patch(
            f"/analytics/figures/{detail['id']}/",
            {"name": "novo nome", "updated_at": detail["updated_at"]},
            format="json",
        )

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["name"], "novo nome")

    # -- permissão / soft delete ----------------------------------------------

    def test_leitor_le_mas_nao_edita_nem_recomputa(self):
        """Toda mutação consulta can_edit_experiment; leitura não.

        O RBAC atual não tem papel "leitor" — membro ativo edita. O mock
        simula a situação "visível mas sem escrita" e garante que as
        views mutáveis passam pela checagem (e a de leitura não).
        """
        from unittest.mock import patch

        figure_id = self._create().json()["id"]

        with patch("fcs_parser.permissions.can_edit_experiment", return_value=False):
            self.assertEqual(
                self.client.get(f"/analytics/figures/{figure_id}/").status_code, 200
            )
            self.assertEqual(
                self.client.get(
                    f"/analytics/experiment/{self.experiment.id}/figures/"
                ).status_code,
                200,
            )
            self.assertEqual(
                self.client.patch(
                    f"/analytics/figures/{figure_id}/", {"name": "x"}, format="json"
                ).status_code,
                403,
            )
            self.assertEqual(
                self.client.post(
                    f"/analytics/figures/{figure_id}/recompute/"
                ).status_code,
                403,
            )
            self.assertEqual(
                self.client.delete(f"/analytics/figures/{figure_id}/").status_code,
                403,
            )
            self.assertEqual(self._create(name="outra").status_code, 403)

    def test_delete_soft_e_listagem_exclui(self):
        figure_id = self._create().json()["id"]

        res = self.client.delete(f"/analytics/figures/{figure_id}/")

        self.assertEqual(res.status_code, 204)
        self.assertEqual(
            self.client.get(f"/analytics/figures/{figure_id}/").status_code, 404
        )
        listing = self.client.get(
            f"/analytics/experiment/{self.experiment.id}/figures/"
        ).json()
        self.assertEqual(listing["results"], [])

    def test_fora_do_escopo_404(self):
        figure_id = self._create().json()["id"]

        self.client.force_authenticate(self.outsider)

        self.assertEqual(
            self.client.get(f"/analytics/figures/{figure_id}/").status_code, 404
        )

    # -- stats_tests (§7.5) ---------------------------------------------------

    def _group_with_values(self, name, values, channel_stats=None):
        """Cria uma amostra por valor, cada uma com gate P1 e stats.

        ``values`` viram percent_of_parent_population (métrica default
        dos testes de stats) — retorna o dict de grupo do spec.
        """
        from analytics.models import AnalysisResult

        ids = []
        for i, value in enumerate(values):
            fd = self._file(self.experiment, f"{name}{i}.fcs")
            fd.data_set = [{"fsc_a": 1.0}]
            fd.save(update_fields=["data_set"])
            gate = self._gate(fd, "P1")
            AnalysisResult.objects.update_or_create(
                gate=gate,
                defaults={
                    "analysis_result": {
                        "summary_metrics": {
                            "count": 10,
                            "percent_of_total_population": value,
                            "percent_of_parent_population": value,
                        },
                        "channel_statistics": channel_stats or {},
                    }
                },
            )
            ids.append(fd.id)
        return {"name": name, "file_data_ids": ids}

    def _stats_spec(self, a_values, b_values, **overrides):
        spec = self._spec(
            groups=[
                self._group_with_values("GA", a_values),
                self._group_with_values("GB", b_values),
            ]
        )
        spec.update(overrides)
        return spec

    def test_metrica_rcv_aceita_e_lida_do_channel_statistics(self):
        res = self._create(spec=self._spec(metric="rcv", channel="fitc_a"))

        self.assertEqual(res.status_code, 201)
        rows = res.json()["result_cache"]["rows"]
        self.assertEqual({r["value"] for r in rows}, {30.0})

    def test_stats_test_valor_invalido_400(self):
        res = self._create(spec=self._spec(stats_test="bayes"))

        self.assertEqual(res.status_code, 400)

    def test_stats_tests_parametrico_anova_e_welch(self):
        spec = self._stats_spec([10.0, 11.0, 12.0], [20.0, 21.0, 22.0])
        spec["stats_test"] = "parametric"

        res = self._create(spec=spec)

        self.assertEqual(res.status_code, 201)
        stats_tests = res.json()["result_cache"]["stats_tests"]
        self.assertEqual(len(stats_tests), 1)
        entry = stats_tests[0]
        self.assertEqual(entry["population"], "P1")
        self.assertEqual(entry["method"], "parametric")
        self.assertEqual(entry["omnibus"]["test"], "one_way_anova")
        self.assertEqual(entry["omnibus"]["df"], [1, 4])
        self.assertIsNotNone(entry["omnibus"]["F"])
        self.assertEqual(len(entry["pairwise"]), 1)
        pair = entry["pairwise"][0]
        self.assertEqual(pair["method"], "welch_t")
        self.assertIsNotNone(pair["t"])
        self.assertIsNotNone(pair["p"])
        self.assertIsNotNone(pair["p_adj"])
        self.assertEqual(entry["n_per_group"], {"GA": 3, "GB": 3})

    def test_stats_tests_nao_parametrico_kruskal_e_mann_whitney(self):
        spec = self._stats_spec([10.0, 11.0, 12.0], [20.0, 21.0, 22.0])
        spec["stats_test"] = "nonparametric"

        res = self._create(spec=spec)

        entry = res.json()["result_cache"]["stats_tests"][0]
        self.assertEqual(entry["method"], "nonparametric")
        self.assertEqual(entry["omnibus"]["test"], "kruskal_wallis")
        self.assertIsNotNone(entry["omnibus"]["H"])
        pair = entry["pairwise"][0]
        self.assertEqual(pair["method"], "mann_whitney_u")
        self.assertIn("U", pair)
        self.assertIsNotNone(pair["p_adj"])

    def test_stats_tests_auto_cai_para_nao_parametrico_com_n_pequeno(self):
        spec = self._stats_spec([10.0, 11.0, 12.0], [20.0, 21.0, 22.0])
        # stats_test omitido → auto; n=3 por grupo → não-paramétrico

        res = self._create(spec=spec)

        entry = res.json()["result_cache"]["stats_tests"][0]
        self.assertEqual(entry["method"], "nonparametric")
        self.assertTrue(any("n < 10" in w for w in entry["warnings"]))

    def test_stats_tests_auto_parametrico_com_n_grande(self):
        spec = self._stats_spec(
            [float(v) for v in range(10, 22)],
            [float(v) for v in range(30, 42)],
        )

        res = self._create(spec=spec)

        entry = res.json()["result_cache"]["stats_tests"][0]
        self.assertEqual(entry["method"], "parametric")
        self.assertEqual(entry["omnibus"]["test"], "one_way_anova")

    def test_stats_tests_bh_corrige_p_adj(self):
        # 3 grupos → 3 pares; p_adj deve ser >= p cru e <= 1
        spec = self._stats_spec(
            [10.0, 11.0, 12.0], [20.0, 21.0, 22.0], stats_test="parametric"
        )
        spec["groups"].append(self._group_with_values("GC", [30.0, 31.0, 32.0]))

        res = self._create(spec=spec)

        entry = res.json()["result_cache"]["stats_tests"][0]
        self.assertEqual(len(entry["pairwise"]), 3)
        for pair in entry["pairwise"]:
            self.assertGreaterEqual(pair["p_adj"], pair["p"])
            self.assertLessEqual(pair["p_adj"], 1.0)

    def test_stats_tests_grupo_n_menor_3_vai_para_warnings(self):
        spec = self._stats_spec(
            [10.0, 11.0, 12.0], [20.0, 21.0], stats_test="parametric"
        )

        res = self._create(spec=spec)

        entry = res.json()["result_cache"]["stats_tests"][0]
        self.assertEqual(entry["n_per_group"], {"GA": 3, "GB": 2})
        self.assertTrue(any("n<3" in w for w in entry["warnings"]))
        # GB excluído → menos de 2 elegíveis → sem teste
        self.assertIsNone(entry["omnibus"])
        self.assertEqual(entry["pairwise"], [])

    def test_stats_tests_lista_por_populacao(self):
        # Cada população do spec tem sua própria bateria de testes.
        groups = [
            self._group_with_values("GA", [10.0, 11.0, 12.0]),
            self._group_with_values("GB", [20.0, 21.0, 22.0]),
        ]
        # gate filho P2 em cada amostra criada pelo helper
        from analytics.models import AnalysisResult

        for group in groups:
            for fd_id in group["file_data_ids"]:
                fd = FileDataModel.objects.get(id=fd_id)
                p1 = GateModel.objects.get(file_data=fd, name="P1")
                child = self._gate(fd, "P2", parent=p1)
                AnalysisResult.objects.update_or_create(
                    gate=child,
                    defaults={
                        "analysis_result": {
                            "summary_metrics": {
                                "count": 5,
                                "percent_of_total_population": 5.0,
                                "percent_of_parent_population": 5.0,
                            },
                            "channel_statistics": {},
                        }
                    },
                )
        spec = self._spec(groups=groups, populations=["P1", "P1 > P2"])

        res = self._create(spec=spec)

        stats_tests = res.json()["result_cache"]["stats_tests"]
        self.assertEqual([e["population"] for e in stats_tests], ["P1", "P1 > P2"])

    # -- stats_test explícitos (card #92) ------------------------------------

    def test_stats_test_t_student_pairwise_sem_omnibus(self):
        spec = self._stats_spec(
            [10.0, 11.0, 12.0], [20.0, 21.0, 22.0], stats_test="t_student"
        )

        res = self._create(spec=spec)

        entry = res.json()["result_cache"]["stats_tests"][0]
        self.assertEqual(entry["method"], "t_student")
        self.assertIsNone(entry["omnibus"])
        pair = entry["pairwise"][0]
        self.assertEqual(pair["method"], "t_student")
        self.assertIn("t", pair)
        self.assertIsNotNone(pair["p_adj"])

    def test_stats_test_t_welch_pairwise_sem_omnibus(self):
        spec = self._stats_spec(
            [10.0, 11.0, 12.0], [20.0, 21.0, 22.0], stats_test="t_welch"
        )

        res = self._create(spec=spec)

        entry = res.json()["result_cache"]["stats_tests"][0]
        self.assertIsNone(entry["omnibus"])
        self.assertEqual(entry["pairwise"][0]["method"], "welch_t")

    def test_stats_test_anova_explicito_com_omnibus(self):
        spec = self._stats_spec(
            [10.0, 11.0, 12.0], [20.0, 21.0, 22.0], stats_test="anova"
        )

        res = self._create(spec=spec)

        entry = res.json()["result_cache"]["stats_tests"][0]
        self.assertEqual(entry["omnibus"]["test"], "one_way_anova")
        self.assertEqual(entry["pairwise"][0]["method"], "welch_t")

    def test_stats_test_kruskal_explicito_com_omnibus(self):
        spec = self._stats_spec(
            [10.0, 11.0, 12.0], [20.0, 21.0, 22.0], stats_test="kruskal_wallis"
        )

        res = self._create(spec=spec)

        entry = res.json()["result_cache"]["stats_tests"][0]
        self.assertEqual(entry["omnibus"]["test"], "kruskal_wallis")
        self.assertEqual(entry["pairwise"][0]["method"], "mann_whitney_u")

    def test_stats_test_mann_whitney_sem_omnibus(self):
        spec = self._stats_spec(
            [10.0, 11.0, 12.0], [20.0, 21.0, 22.0], stats_test="mann_whitney"
        )

        res = self._create(spec=spec)

        entry = res.json()["result_cache"]["stats_tests"][0]
        self.assertEqual(entry["method"], "mann_whitney")
        self.assertIsNone(entry["omnibus"])
        pair = entry["pairwise"][0]
        self.assertEqual(pair["method"], "mann_whitney_u")
        self.assertIn("U", pair)

    def test_distribution_nao_tem_stats_tests(self):
        spec = self._spec(populations=["P1"], channel="fitc_a")

        res = self._create(chart_type="distribution", spec=spec)

        self.assertEqual(res.status_code, 201)
        self.assertNotIn("stats_tests", res.json()["result_cache"])

    @override_settings(ANALYSIS_FIGURES_ENABLED=False)
    def test_feature_flag_desligada_retorna_503(self):
        url = f"/analytics/experiment/{self.experiment.id}/figures/"
        self.assertEqual(self.client.get(url).status_code, 503)
        self.assertEqual(
            self.client.post(url, {}, format="json").status_code,
            503,
        )
