from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import User
from analytics.models import DashboardModel, GateModel
from analytics.tasks import recalculate_gate_analysis
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
