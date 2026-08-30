from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import User
from analytics.models import DashboardModel, GateModel
from fcs_parser.models import ExperimentModel, FileDataModel, FileModel


class GateScopeTestCase(TestCase):
    """BE-03/BE-04: exclusão em lote e propagação de nome/cor por escopo."""

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

        self.assertEqual(res.status_code, 403)
        self.assertTrue(GateModel.objects.filter(id=self.source.id).exists())

    def _patch_gate(self, gate, **payload):
        return self.client.patch(f"/analytics/gate/{gate.id}", payload, format="json")

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

    def test_patch_experiment_scope_requires_name_or_color(self):
        res = self._patch_gate(self.source, scope="experiment", plot_config={"a": 1})

        self.assertEqual(res.status_code, 400)

    def test_patch_experiment_scope_requires_permission(self):
        self.client.force_authenticate(self.outsider)

        res = self._patch_gate(self.source, name="CD4+", scope="experiment")

        self.assertEqual(res.status_code, 403)
        self.copy_b.refresh_from_db()
        self.assertEqual(self.copy_b.name, "P1")
