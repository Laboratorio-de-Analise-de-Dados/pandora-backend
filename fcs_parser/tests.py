import os
import shutil
import tempfile
import zipfile
from io import StringIO
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import User
from fcs_parser.models import (
    ExperimentModel,
    FileDataModel,
    FileModel,
    SubsampleModel,
)
from fcs_parser.services.process_experiment_file import (
    subsample_for_path,
    extract_fcs_from_zip,
)
from fcs_parser.services.repair_source_path import repair_experiment_source_paths
from utils.validators import experiment_file_extension


class ExperimentFileExtensionTestCase(TestCase):
    """BE-05: upload aceita ZIP com vários .fcs ou um .fcs solto."""

    def test_accepts_zip_and_fcs_ignoring_case(self):
        self.assertEqual(experiment_file_extension("amostras.zip"), ".zip")
        self.assertEqual(experiment_file_extension("amostra.FCS"), ".fcs")

    def test_rejects_other_extensions_and_empty_names(self):
        for invalid in ["amostra.csv", "amostra", "", None, "  "]:
            with self.assertRaises(ValidationError):
                experiment_file_extension(invalid)


class ExperimentInitFileNameTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="dono", email="dono@pandora.test", password="senha-forte-123"
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def _init(self, **payload):
        return self.client.post("/experiment/init/", payload, format="json")

    def test_rejects_unsupported_extension(self):
        res = self._init(
            title="exp", type="tipo", totalChunks=1, fileName="amostra.csv"
        )

        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data["detail"], "Envie um arquivo .zip ou .fcs.")

    def test_accepts_standalone_fcs(self):
        res = self._init(
            title="exp", type="tipo", totalChunks=1, fileName="amostra.fcs"
        )

        self.assertEqual(res.status_code, 201)

    def test_file_name_is_optional_for_older_clients(self):
        res = self._init(title="exp-sem-nome", type="tipo", totalChunks=1)

        self.assertEqual(res.status_code, 201)


class SubsampleExtractionTestCase(TestCase):
    """Identidade da amostra é o caminho dentro do ZIP, não só o nome."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="eng", email="eng@pandora.test", password="senha-forte-123"
        )
        self.experiment = ExperimentModel.objects.create(
            title="exp-subsample", type="tipo", created_by=self.user
        )

    def test_creates_one_subsample_per_zip_directory(self):
        primeiro = subsample_for_path(self.experiment, "tempo_1/a1.fcs")
        segundo = subsample_for_path(self.experiment, "tempo_2/a1.fcs")
        mesmo = subsample_for_path(self.experiment, "tempo_1/a2.fcs")

        self.assertNotEqual(primeiro.id, segundo.id)
        self.assertEqual(primeiro.id, mesmo.id)
        self.assertEqual(primeiro.name, "tempo_1")
        self.assertEqual(SubsampleModel.objects.count(), 2)

    def test_file_at_zip_root_has_no_subsample(self):
        self.assertIsNone(subsample_for_path(self.experiment, "a1.fcs"))
        self.assertEqual(SubsampleModel.objects.count(), 0)

    def test_same_name_in_different_directories_coexists(self):
        file_model = FileModel.objects.create(
            file_name="amostras.zip", experiment=self.experiment
        )
        for path in ["tempo_1/a1.fcs", "tempo_2/a1.fcs"]:
            FileDataModel.objects.create(
                headers={},
                experiment=self.experiment,
                file_name="a1.fcs",
                source_path=path,
                subsample=subsample_for_path(self.experiment, path),
                file=file_model,
            )

        subsamples = {
            f.source_path: f.subsample.name
            for f in FileDataModel.objects.filter(experiment=self.experiment)
        }
        self.assertEqual(
            subsamples, {"tempo_1/a1.fcs": "tempo_1", "tempo_2/a1.fcs": "tempo_2"}
        )

    def test_duplicated_source_path_is_rejected(self):
        file_model = FileModel.objects.create(
            file_name="amostras.zip", experiment=self.experiment
        )
        kwargs = dict(
            headers={},
            experiment=self.experiment,
            file_name="a1.fcs",
            source_path="tempo_1/a1.fcs",
            file=file_model,
        )
        FileDataModel.objects.create(**kwargs)

        with self.assertRaises(IntegrityError):
            FileDataModel.objects.create(**kwargs)

    def test_extract_uses_the_exact_relative_path(self):
        tmp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp_dir, True)
        zip_path = os.path.join(tmp_dir, "amostras.zip")
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("tempo_1/a1.fcs", "primeiro")
            zf.writestr("tempo_2/a1.fcs", "segundo")
        self.experiment.zip_path = zip_path
        self.experiment.save(update_fields=["zip_path"])

        extracted = extract_fcs_from_zip(self.experiment, "tempo_2/a1.fcs")

        self.assertIsNotNone(extracted)
        with open(extracted) as f:
            self.assertEqual(f.read(), "segundo")


class SubsampleApiTestCase(TestCase):
    """O cliente pode criar, renomear e remanejar subsamples pela UI."""

    def setUp(self):
        self.owner = User.objects.create_user(
            username="dono", email="dono@pandora.test", password="senha-forte-123"
        )
        self.stranger = User.objects.create_user(
            username="outro", email="outro@pandora.test", password="senha-forte-123"
        )
        self.experiment = ExperimentModel.objects.create(
            title="exp-api", type="tipo", created_by=self.owner
        )
        self.file_model = FileModel.objects.create(
            file_name="amostras.zip", experiment=self.experiment
        )
        self.subsample = SubsampleModel.objects.create(
            experiment=self.experiment, name="tempo_1", source_path="tempo_1"
        )
        self.file_data = FileDataModel.objects.create(
            headers={},
            experiment=self.experiment,
            file_name="a1.fcs",
            source_path="tempo_1/a1.fcs",
            subsample=self.subsample,
            file=self.file_model,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.owner)

    def list_url(self):
        return f"/experiment/{self.experiment.id}/subsamples/"

    def detail_url(self, subsample=None):
        pk = (subsample or self.subsample).id
        return f"/experiment/{self.experiment.id}/subsamples/{pk}/"

    def move_url(self):
        return f"/experiment/file/{self.file_data.id}/subsample"

    def test_lists_subsamples_with_active_file_count(self):
        response = self.client.get(self.list_url())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["name"], "tempo_1")
        self.assertEqual(response.data[0]["files_count"], 1)

    def test_client_creates_subsample_manually(self):
        response = self.client.post(
            self.list_url(), {"name": "controles"}, format="json"
        )

        self.assertEqual(response.status_code, 201)
        criado = SubsampleModel.objects.get(name="controles")
        self.assertEqual(criado.source_path, "")
        self.assertEqual(criado.created_by, self.owner)

    def test_duplicated_subsample_name_is_rejected(self):
        response = self.client.post(
            self.list_url(), {"name": "tempo_1"}, format="json"
        )

        self.assertEqual(response.status_code, 400)

    def test_client_renames_subsample(self):
        response = self.client.patch(
            self.detail_url(), {"name": "24h"}, format="json"
        )

        self.assertEqual(response.status_code, 200)
        self.subsample.refresh_from_db()
        self.assertEqual(self.subsample.name, "24h")
        self.assertEqual(self.subsample.source_path, "tempo_1")

    def test_delete_only_inactivates_and_ungroups_files(self):
        response = self.client.delete(self.detail_url())

        self.assertEqual(response.status_code, 204)
        self.subsample.refresh_from_db()
        self.file_data.refresh_from_db()
        self.assertFalse(self.subsample.active)
        self.assertIsNone(self.file_data.subsample)

    def test_client_moves_file_between_subsamples(self):
        destino = SubsampleModel.objects.create(
            experiment=self.experiment, name="tempo_2", source_path="tempo_2"
        )

        response = self.client.patch(
            self.move_url(), {"subsample": destino.id}, format="json"
        )

        self.assertEqual(response.status_code, 200)
        self.file_data.refresh_from_db()
        self.assertEqual(self.file_data.subsample_id, destino.id)
        self.assertEqual(self.file_data.source_path, "tempo_1/a1.fcs")

    def test_client_ungroups_file(self):
        response = self.client.patch(
            self.move_url(), {"subsample": None}, format="json"
        )

        self.assertEqual(response.status_code, 200)
        self.file_data.refresh_from_db()
        self.assertIsNone(self.file_data.subsample)

    def test_subsample_from_another_experiment_is_rejected(self):
        outro = ExperimentModel.objects.create(
            title="outro-exp", type="tipo", created_by=self.owner
        )
        alheio = SubsampleModel.objects.create(
            experiment=outro, name="tempo_1", source_path="tempo_1"
        )

        response = self.client.patch(
            self.move_url(), {"subsample": alheio.id}, format="json"
        )

        self.assertEqual(response.status_code, 400)
        self.file_data.refresh_from_db()
        self.assertEqual(self.file_data.subsample_id, self.subsample.id)

    def test_user_without_access_cannot_change_subsamples(self):
        self.client.force_authenticate(self.stranger)

        self.assertEqual(
            self.client.post(self.list_url(), {"name": "x"}, format="json").status_code,
            403,
        )
        self.assertEqual(
            self.client.patch(self.detail_url(), {"name": "x"}, format="json").status_code,
            403,
        )
        self.assertEqual(
            self.client.patch(
                self.move_url(), {"subsample": None}, format="json"
            ).status_code,
            403,
        )


class RepairSourcePathTestCase(TestCase):
    """Backfill pelo ZIP: o caminho perdido é redescoberto na fonte de verdade."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="migr", email="migr@pandora.test", password="senha-forte-123"
        )
        self.experiment = ExperimentModel.objects.create(
            title="exp-legado", type="tipo", created_by=self.user
        )
        self.file_model = FileModel.objects.create(
            file_name="amostras.zip", experiment=self.experiment
        )

    def _zip(self, *entries: str) -> str:
        tmp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp_dir, True)
        zip_path = os.path.join(tmp_dir, "amostras.zip")
        with zipfile.ZipFile(zip_path, "w") as zf:
            for entry in entries:
                zf.writestr(entry, entry)
        self.experiment.zip_path = zip_path
        self.experiment.save(update_fields=["zip_path"])
        return zip_path

    def _legacy(self, file_name: str, source_path: str = "") -> FileDataModel:
        return FileDataModel.objects.create(
            headers={},
            experiment=self.experiment,
            file_name=file_name,
            source_path=source_path,
            file=self.file_model,
        )

    def test_unique_name_in_zip_is_remapped_keeping_the_row(self):
        self._zip("tempo_1/a1.fcs", "tempo_1/a2.fcs")
        legacy = self._legacy("a2.fcs")

        report = repair_experiment_source_paths(self.experiment)

        legacy.refresh_from_db()
        self.assertEqual(legacy.source_path, "tempo_1/a2.fcs")
        self.assertEqual(legacy.subsample.name, "tempo_1")
        self.assertTrue(legacy.active)
        self.assertEqual(report.remapped, [(legacy.id, "tempo_1/a2.fcs")])

    def test_dry_run_reports_without_writing(self):
        self._zip("tempo_1/a2.fcs")
        legacy = self._legacy("a2.fcs")

        report = repair_experiment_source_paths(self.experiment, dry_run=True)

        legacy.refresh_from_db()
        self.assertEqual(legacy.source_path, "")
        self.assertEqual(report.remapped, [(legacy.id, "tempo_1/a2.fcs")])

    def test_homonyms_are_ambiguous_without_recreate(self):
        self._zip("tempo_1/a1.fcs", "tempo_2/a1.fcs")
        legacy = self._legacy("a1.fcs")

        report = repair_experiment_source_paths(self.experiment)

        legacy.refresh_from_db()
        self.assertEqual(legacy.source_path, "")
        self.assertTrue(legacy.active)
        self.assertEqual(
            sorted(report.ambiguous), ["tempo_1/a1.fcs", "tempo_2/a1.fcs"]
        )
        self.assertEqual(report.recreated, [])

    @patch("fcs_parser.services.repair_source_path.readfcs.view")
    def test_recreate_inactivates_ambiguous_and_rebuilds_from_zip(self, view):
        view.return_value = ({"$TOT": "10"}, None)
        self._zip("tempo_1/a1.fcs", "tempo_2/a1.fcs")
        legacy = self._legacy("a1.fcs")

        repair_experiment_source_paths(self.experiment, recreate=True)

        legacy.refresh_from_db()
        self.assertFalse(legacy.active)
        self.assertIsNotNone(legacy.deactivated_at)
        recreated = FileDataModel.objects.filter(
            experiment=self.experiment, active=True
        ).order_by("source_path")
        self.assertEqual(
            [f.source_path for f in recreated], ["tempo_1/a1.fcs", "tempo_2/a1.fcs"]
        )
        self.assertEqual(
            [f.subsample.name for f in recreated], ["tempo_1", "tempo_2"]
        )
        self.assertEqual(recreated[0].headers, {"$TOT": "10"})

    def test_rows_already_matching_the_zip_are_left_alone(self):
        self._zip("tempo_1/a1.fcs", "tempo_2/a1.fcs")
        ok = self._legacy("a1.fcs", source_path="tempo_1/a1.fcs")
        legacy = self._legacy("a1.fcs")

        report = repair_experiment_source_paths(self.experiment)

        ok.refresh_from_db()
        legacy.refresh_from_db()
        self.assertEqual(ok.source_path, "tempo_1/a1.fcs")
        self.assertEqual(legacy.source_path, "tempo_2/a1.fcs")
        self.assertEqual(report.remapped, [(legacy.id, "tempo_2/a1.fcs")])

    def test_experiment_without_zip_is_skipped(self):
        legacy = self._legacy("a1.fcs")

        report = repair_experiment_source_paths(self.experiment)

        legacy.refresh_from_db()
        self.assertEqual(legacy.source_path, "")
        self.assertEqual(report.remapped, [])

    def test_command_runs_over_every_experiment(self):
        self._zip("tempo_1/a2.fcs")
        legacy = self._legacy("a2.fcs")

        call_command("repair_source_path", stdout=StringIO())

        legacy.refresh_from_db()
        self.assertEqual(legacy.source_path, "tempo_1/a2.fcs")
