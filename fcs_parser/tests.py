import hashlib
import io
import os
import shutil
import tempfile
import zipfile
from io import StringIO
from unittest.mock import patch

import pandas as pd
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import IntegrityError
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import Membership, Organization, Role, User
from analytics.models import AnalysisResult, DashboardModel, GateModel
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
        tmp_dir = tempfile.mkdtemp(dir=settings.MEDIA_ROOT)
        self.addCleanup(shutil.rmtree, tmp_dir, True)
        zip_path = os.path.join(tmp_dir, "amostras.zip")
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("tempo_1/a1.fcs", "primeiro")
            zf.writestr("tempo_2/a1.fcs", "segundo")
        self.experiment.zip_path = zip_path
        self.experiment.save(update_fields=["zip_path"])
        upload = FileModel.objects.create(
            file_name="amostras.zip", file=zip_path, experiment=self.experiment
        )

        extracted = extract_fcs_from_zip(upload, "tempo_2/a1.fcs")

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
        response = self.client.post(self.list_url(), {"name": "tempo_1"}, format="json")

        self.assertEqual(response.status_code, 400)

    def test_client_renames_subsample(self):
        response = self.client.patch(self.detail_url(), {"name": "24h"}, format="json")

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
        # Outsider recebe 404 (não enxerga o experimento), não 403.
        self.client.force_authenticate(self.stranger)

        self.assertEqual(
            self.client.post(self.list_url(), {"name": "x"}, format="json").status_code,
            404,
        )
        self.assertEqual(
            self.client.patch(
                self.detail_url(), {"name": "x"}, format="json"
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.patch(
                self.move_url(), {"subsample": None}, format="json"
            ).status_code,
            404,
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
        self.assertEqual(sorted(report.ambiguous), ["tempo_1/a1.fcs", "tempo_2/a1.fcs"])
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
        self.assertEqual([f.subsample.name for f in recreated], ["tempo_1", "tempo_2"])
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


class ContentGuidTestCase(TestCase):
    """BE-10: `content_guid` é único dentro do experimento, livre entre eles."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="dono", email="dono@pandora.test", password="senha-forte-123"
        )
        self.exp_a = ExperimentModel.objects.create(
            title="exp-a", type="t", created_by=self.user
        )
        self.exp_b = ExperimentModel.objects.create(
            title="exp-b", type="t", created_by=self.user
        )
        self.fm_a = FileModel.objects.create(file_name="a.zip", experiment=self.exp_a)
        self.fm_b = FileModel.objects.create(file_name="b.zip", experiment=self.exp_b)

    def test_same_guid_twice_in_one_experiment_is_rejected(self):
        kwargs = dict(
            headers={"guid": "g1"},
            content_guid="g1",
            experiment=self.exp_a,
            file_name="a1.fcs",
            file=self.fm_a,
        )
        FileDataModel.objects.create(**kwargs)
        with self.assertRaises(IntegrityError):
            FileDataModel.objects.create(**kwargs)

    def test_same_guid_across_experiments_is_allowed(self):
        FileDataModel.objects.create(
            headers={"guid": "g1"},
            content_guid="g1",
            experiment=self.exp_a,
            file_name="a1.fcs",
            file=self.fm_a,
        )
        FileDataModel.objects.create(
            headers={"guid": "g1"},
            content_guid="g1",
            experiment=self.exp_b,
            file_name="a1.fcs",
            file=self.fm_b,
        )
        self.assertEqual(FileDataModel.objects.filter(content_guid="g1").count(), 2)

    def test_files_without_guid_coexist(self):
        for name in ["a.fcs", "b.fcs"]:
            FileDataModel.objects.create(
                headers={},
                experiment=self.exp_a,
                file_name=name,
                file=self.fm_a,
            )
        self.assertEqual(FileDataModel.objects.filter(experiment=self.exp_a).count(), 2)


class ExperimentCopyApiTestCase(TestCase):
    """BE-11: copiar cria análise independente reutilizando o blob físico."""

    def setUp(self):
        self.owner = User.objects.create_user(
            username="dono", email="dono@pandora.test", password="senha-forte-123"
        )
        self.stranger = User.objects.create_user(
            username="outro", email="outro@pandora.test", password="senha-forte-123"
        )
        self.org = Organization.objects.create(name="Lab X", org_type="lab")
        self.member_role = Role.objects.create(name="member")
        self.admin_role = Role.objects.create(name="org_admin")

        self.source = ExperimentModel.objects.create(
            title="exp-origem",
            type="t",
            created_by=self.owner,
            status="done",
            zip_path="/tmp/origem.zip",
        )
        self.file_model = FileModel.objects.create(
            file_name="origem.zip",
            file="origem.zip",
            sha256="a" * 64,
            experiment=self.source,
        )
        self.subsample = SubsampleModel.objects.create(
            experiment=self.source, name="tempo_1", source_path="tempo_1"
        )
        self.file_data = FileDataModel.objects.create(
            headers={"guid": "g1"},
            content_guid="g1",
            experiment=self.source,
            file_name="a1.fcs",
            source_path="tempo_1/a1.fcs",
            subsample=self.subsample,
            file=self.file_model,
        )
        self.dashboard = DashboardModel.objects.create(
            file_data=self.file_data, name="dash", dashboard_config={}
        )
        self.gate = GateModel.objects.create(
            file_data=self.file_data,
            name="P1",
            gate_coordinates={"x": [1, 2], "y": [3, 4]},
            dashboard=self.dashboard,
            created_by=self.owner,
        )
        AnalysisResult.objects.create(gate=self.gate, analysis_result={"count": 10})

        self.client = APIClient()
        self.client.force_authenticate(self.owner)

    def _copy(self, user=None, **payload):
        client = APIClient()
        client.force_authenticate(user or self.owner)
        return client.post(f"/experiment/{self.source.id}/copy", payload, format="json")

    def test_copy_creates_independent_rows_sharing_the_blob(self):
        res = self._copy()

        self.assertEqual(res.status_code, 201)
        clone = ExperimentModel.objects.get(id=res.data["id"])
        self.assertEqual(clone.title, "exp-origem_copia")
        self.assertIsNone(clone.organization_id)
        self.assertEqual(clone.created_by, self.owner)
        self.assertEqual(clone.zip_path, self.source.zip_path)

        clone_fm = FileModel.objects.get(experiment=clone)
        self.assertEqual(clone_fm.file.name, self.file_model.file.name)
        self.assertEqual(clone_fm.sha256, "a" * 64)
        self.assertNotEqual(clone_fm.id, self.file_model.id)

        clone_fd = FileDataModel.objects.get(experiment=clone)
        self.assertEqual(clone_fd.content_guid, "g1")
        self.assertEqual(clone_fd.subsample.name, "tempo_1")
        self.assertIsNone(clone_fd.parquet_path)
        # Mesmo blob, linhas independentes.
        self.assertNotEqual(clone_fd.id, self.file_data.id)
        self.assertNotEqual(clone_fd.file_id, self.file_data.file_id)

        clone_gate = GateModel.objects.get(file_data=clone_fd)
        self.assertEqual(clone_gate.name, "P1")
        self.assertEqual(clone_gate.copied_from_id, self.gate.id)
        self.assertEqual(clone_gate.analysis_result.analysis_result, {"count": 10})

    def test_copy_preserves_gate_tree_and_title_suffix(self):
        child = GateModel.objects.create(
            file_data=self.file_data,
            name="P2",
            gate_coordinates={},
            dashboard=self.dashboard,
            parent=self.gate,
        )
        res = self._copy()

        clone = ExperimentModel.objects.get(id=res.data["id"])
        clone_fd = FileDataModel.objects.get(experiment=clone)
        root = GateModel.objects.get(file_data=clone_fd, parent__isnull=True)
        self.assertEqual(root.children.get().name, "P2")
        self.assertNotEqual(root.children.get().id, child.id)

        # Segunda cópia resolve a colisão de título com sufixo.
        res2 = self._copy()
        self.assertEqual(res2.status_code, 201)
        self.assertEqual(res2.data["title"], "exp-origem_copia_2")

    def test_copy_to_org_requires_membership(self):
        res = self._copy(organization_id=self.org.id)
        self.assertEqual(res.status_code, 403)

        Membership.objects.create(
            user=self.owner,
            organization=self.org,
            role=self.member_role,
            status="active",
        )
        res = self._copy(organization_id=self.org.id)
        self.assertEqual(res.status_code, 201)
        clone = ExperimentModel.objects.get(id=res.data["id"])
        self.assertEqual(clone.organization_id, self.org.id)

    def test_copy_from_org_by_member_allowed_stranger_blocked(self):
        self.source.organization = self.org
        self.source.save(update_fields=["organization"])
        Membership.objects.create(
            user=self.stranger,
            organization=self.org,
            role=self.member_role,
            status="active",
        )
        res = self._copy(user=self.stranger)
        self.assertEqual(res.status_code, 201)

        nobody = User.objects.create_user(
            username="ninguem", email="n@pandora.test", password="senha-forte-123"
        )
        res = self._copy(user=nobody)
        self.assertEqual(res.status_code, 404)


class ExperimentMoveApiTestCase(TestCase):
    """BE-11: mover troca o contexto sem duplicar nada — dono/admin na origem."""

    def setUp(self):
        self.owner = User.objects.create_user(
            username="dono", email="dono@pandora.test", password="senha-forte-123"
        )
        self.admin = User.objects.create_user(
            username="admin", email="admin@pandora.test", password="senha-forte-123"
        )
        self.member = User.objects.create_user(
            username="membro", email="membro@pandora.test", password="senha-forte-123"
        )
        self.org = Organization.objects.create(name="Lab X", org_type="lab")
        self.other_org = Organization.objects.create(name="Lab Y", org_type="lab")
        member_role = Role.objects.create(name="member")
        admin_role = Role.objects.create(name="org_admin")
        for user, role in [(self.admin, admin_role), (self.member, member_role)]:
            Membership.objects.create(
                user=user, organization=self.org, role=role, status="active"
            )
        Membership.objects.create(
            user=self.owner,
            organization=self.other_org,
            role=member_role,
            status="active",
        )
        Membership.objects.create(
            user=self.member,
            organization=self.other_org,
            role=member_role,
            status="active",
        )

        self.experiment = ExperimentModel.objects.create(
            title="exp", type="t", created_by=self.owner, organization=self.org
        )
        FileModel.objects.create(file_name="e.zip", experiment=self.experiment)

    def _patch(self, user, **payload):
        client = APIClient()
        client.force_authenticate(user)
        return client.patch(
            f"/experiment/{self.experiment.id}/", payload, format="json"
        )

    def test_owner_moves_experiment_to_other_org(self):
        res = self._patch(self.owner, organization_id=self.other_org.id)

        self.assertEqual(res.status_code, 200)
        self.experiment.refresh_from_db()
        self.assertEqual(self.experiment.organization_id, self.other_org.id)
        self.assertEqual(
            FileModel.objects.filter(experiment=self.experiment).count(), 1
        )

    def test_org_admin_moves_experiment(self):
        res = self._patch(self.admin, organization_id=None)

        self.assertEqual(res.status_code, 200)
        self.experiment.refresh_from_db()
        self.assertIsNone(self.experiment.organization_id)

    def test_plain_member_cannot_move(self):
        res = self._patch(self.member, organization_id=self.other_org.id)
        self.assertEqual(res.status_code, 403)

        self.experiment.refresh_from_db()
        self.assertEqual(self.experiment.organization_id, self.org.id)

    def test_move_to_org_without_membership_is_rejected(self):
        res = self._patch(self.owner, organization_id=self.org.id)
        # Dono não é membro da própria org neste fixture — mas como origem ele
        # pode mover; o destino é a org onde ele já está. Caso real de rejeição:
        res = self._patch(self.owner, organization_id=999)
        self.assertEqual(res.status_code, 400)

    def test_move_permite_mesmo_titulo_de_experimento_inativo(self):
        # BE-16: título único só vale entre ativos — inativo não bloqueia.
        ExperimentModel.objects.create(
            title="exp", type="t", created_by=self.owner, active=False
        )

        res = self._patch(self.owner, organization_id=None)

        self.assertEqual(res.status_code, 200)
        self.experiment.refresh_from_db()
        self.assertIsNone(self.experiment.organization_id)

    def test_move_rejeita_mesmo_titulo_de_experimento_ativo(self):
        ExperimentModel.objects.create(title="exp", type="t", created_by=self.owner)

        res = self._patch(self.owner, organization_id=None)

        self.assertEqual(res.status_code, 400)
        self.experiment.refresh_from_db()
        self.assertEqual(self.experiment.organization_id, self.org.id)


class FileHashCheckApiTestCase(TestCase):
    """BE-12: check-hash informa duplicata; nunca bloqueia o upload."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="dono", email="dono@pandora.test", password="senha-forte-123"
        )
        self.experiment = ExperimentModel.objects.create(
            title="exp", type="t", created_by=self.user
        )
        FileModel.objects.create(
            file_name="amostras.zip", sha256="b" * 64, experiment=self.experiment
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_reports_existing_hash(self):
        res = self.client.post(
            "/experiment/check-hash/", {"sha256": "b" * 64}, format="json"
        )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data["exists"])
        self.assertEqual(res.data["file_name"], "amostras.zip")

    def test_unknown_hash(self):
        res = self.client.post(
            "/experiment/check-hash/", {"sha256": "c" * 64}, format="json"
        )
        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.data["exists"])

    def test_rejects_malformed_hash(self):
        res = self.client.post(
            "/experiment/check-hash/", {"sha256": "nope"}, format="json"
        )
        self.assertEqual(res.status_code, 400)


class ExperimentFilesApiTestCase(TestCase):
    """Adicionar arquivos (ZIP ou .fcs) a um experimento existente.

    Fluxo: files/init → files/upload-chunk → files/complete. `.fcs` solto é
    aglutinado num ZIP; dedup é escopado ao experimento (skip, nunca erro).
    """

    def setUp(self):
        self.user = User.objects.create_user(
            username="dono", email="dono@pandora.test", password="senha-forte-123"
        )
        self.other = User.objects.create_user(
            username="outro",
            email="outro@pandora.test",
            password="senha-forte-123",
        )
        self.experiment = ExperimentModel.objects.create(
            title="exp", type="t", created_by=self.user, status="done"
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

        self.media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media, True)
        override = self.settings(MEDIA_ROOT=self.media)
        override.enable()
        self.addCleanup(override.disable)

    def _zip_bytes(self, entries: dict) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            for name, data in entries.items():
                zf.writestr(name, data)
        return buf.getvalue()

    def _upload(self, file_name: str, payload: bytes) -> int:
        init = self.client.post(
            f"/experiment/{self.experiment.id}/files/init",
            {"fileName": file_name, "totalChunks": 1},
            format="json",
        )
        file_id = init.data["fileId"]
        self.client.post(
            "/experiment/files/upload-chunk/",
            {
                "fileId": file_id,
                "chunkIndex": 0,
                "chunk": SimpleUploadedFile("c0", payload),
            },
        )
        return file_id

    def test_init_creates_pending_upload(self):
        res = self.client.post(
            f"/experiment/{self.experiment.id}/files/init",
            {"fileName": "novo.zip", "totalChunks": 3},
            format="json",
        )

        self.assertEqual(res.status_code, 201)
        upload = FileModel.objects.get(id=res.data["fileId"])
        self.assertEqual(upload.experiment, self.experiment)
        self.assertEqual(upload.total_chunks, 3)
        self.assertFalse(upload.file)

    def test_init_requires_edit_permission(self):
        # `other` não é membro/dono: outsider recebe 404 (não enxerga o
        # experimento), coerente com o restante da política de acesso.
        client = APIClient()
        client.force_authenticate(self.other)
        res = client.post(
            f"/experiment/{self.experiment.id}/files/init",
            {"fileName": "novo.zip", "totalChunks": 1},
            format="json",
        )
        self.assertEqual(res.status_code, 404)

    @patch("fcs_parser.views.extract_metadata_from_zip")
    def test_complete_wraps_standalone_fcs_into_zip(self, mock_extract):
        file_id = self._upload("amostra.fcs", b"fcs-bytes")
        res = self.client.post(
            "/experiment/files/complete/",
            {"fileId": file_id, "fileName": "amostra.fcs"},
            format="json",
        )

        self.assertEqual(res.status_code, 200)
        upload = FileModel.objects.get(id=file_id)
        self.assertTrue(upload.file.name.endswith(".zip"))
        self.assertTrue(os.path.exists(upload.file.path))
        with zipfile.ZipFile(upload.file.path) as zf:
            self.assertEqual(zf.read("amostra.fcs"), b"fcs-bytes")
        self.assertIsNotNone(upload.sha256)
        mock_extract.assert_called_once()

    @patch("fcs_parser.services.process_experiment_file.readfcs")
    def test_duplicate_sample_within_experiment_is_skipped(self, mock_readfcs):
        mock_readfcs.view.return_value = ({}, None)
        payload = b"dup-bytes"
        sha = hashlib.sha256(payload).hexdigest()
        existing_upload = FileModel.objects.create(
            file_name="velho.zip", experiment=self.experiment
        )
        FileDataModel.objects.create(
            headers={},
            experiment=self.experiment,
            file_name="a1.fcs",
            source_path="a1.fcs",
            content_sha256=sha,
            file=existing_upload,
        )

        file_id = self._upload("novo.zip", self._zip_bytes({"a1.fcs": payload}))
        res = self.client.post(
            "/experiment/files/complete/",
            {"fileId": file_id, "fileName": "novo.zip"},
            format="json",
        )

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["added"], 0)
        self.assertEqual(res.data["skipped"], ["a1.fcs"])

    @patch("fcs_parser.services.process_experiment_file.readfcs")
    def test_same_sample_allowed_in_another_experiment(self, mock_readfcs):
        mock_readfcs.view.return_value = ({}, None)
        mock_readfcs.ReadFCS.return_value.channels = pd.DataFrame({"PnN": ["FL1"]})
        payload = b"same-bytes"
        sha = hashlib.sha256(payload).hexdigest()

        other_exp = ExperimentModel.objects.create(
            title="outro", type="t", created_by=self.other, status="done"
        )
        FileDataModel.objects.create(
            headers={},
            experiment=other_exp,
            file_name="a1.fcs",
            source_path="a1.fcs",
            content_sha256=sha,
            file=FileModel.objects.create(file_name="x.zip", experiment=other_exp),
        )

        file_id = self._upload("novo.zip", self._zip_bytes({"a1.fcs": payload}))
        res = self.client.post(
            "/experiment/files/complete/",
            {"fileId": file_id, "fileName": "novo.zip"},
            format="json",
        )

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["added"], 1)
        self.assertEqual(res.data["skipped"], [])

    def test_download_rebuilds_zip_by_subsample(self):
        zip_path = os.path.join(self.media, "origem.zip")
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("pasta/a1.fcs", b"conteudo-a1")
            zf.writestr("a2.fcs", b"conteudo-a2")
        upload = FileModel.objects.create(
            file_name="origem.zip", file=zip_path, experiment=self.experiment
        )
        sub = SubsampleModel.objects.create(
            experiment=self.experiment, name="tempo_1", source_path="pasta"
        )
        FileDataModel.objects.create(
            headers={},
            experiment=self.experiment,
            file_name="a1.fcs",
            source_path="pasta/a1.fcs",
            subsample=sub,
            file=upload,
        )
        FileDataModel.objects.create(
            headers={},
            experiment=self.experiment,
            file_name="a2.fcs",
            source_path="a2.fcs",
            file=upload,
        )

        res = self.client.get(f"/experiment/{self.experiment.id}/download")

        self.assertEqual(res.status_code, 200)
        content = io.BytesIO(b"".join(res.streaming_content))
        with zipfile.ZipFile(content) as zf:
            names = sorted(zf.namelist())
            self.assertEqual(names, ["a2.fcs", "tempo_1/a1.fcs"])
            self.assertEqual(zf.read("tempo_1/a1.fcs"), b"conteudo-a1")


class ScopedAccessTestCase(TestCase):
    """ADR-0014: todo lookup por id passa por queryset escopado.

    Quem está fora do experimento recebe 404 (não 200 com dados, não 403
    vazando existência) em qualquer endpoint de amostra/upload/experimento.
    """

    def setUp(self):
        self.owner = User.objects.create_user(
            username="dono", email="dono@pandora.test", password="senha-forte-123"
        )
        self.outsider = User.objects.create_user(
            username="fora", email="fora@pandora.test", password="senha-forte-123"
        )
        self.experiment = ExperimentModel.objects.create(
            title="exp", type="tipo", created_by=self.owner
        )
        self.upload = FileModel.objects.create(
            file_name="amostras.zip", experiment=self.experiment
        )
        self.file_data = FileDataModel.objects.create(
            headers={},
            experiment=self.experiment,
            file_name="a1.fcs",
            source_path="a1.fcs",
            file=self.upload,
        )
        self.client = APIClient()

    def test_outsider_lista_arquivos_vazia(self):
        self.client.force_authenticate(self.outsider)
        res = self.client.get(f"/experiment/list/data/{self.experiment.id}/")

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data, [])

    def test_outsider_404_nas_leituras_de_amostra(self):
        self.client.force_authenticate(self.outsider)
        urls = [
            f"/experiment/file/{self.file_data.id}/list",
            f"/experiment/file/{self.file_data.id}/headers",
            f"/experiment/file/{self.file_data.id}/stats",
            f"/experiment/file/{self.file_data.id}/density?x=FSC-A&y=SSC-A",
        ]
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 404)

    def test_outsider_404_nas_escritas_de_amostra(self):
        self.client.force_authenticate(self.outsider)
        urls = [
            f"/experiment/file/{self.file_data.id}/disable",
            f"/experiment/file/{self.file_data.id}/enable",
            f"/experiment/file/{self.file_data.id}/recompute",
            f"/experiment/file/{self.upload.id}/process",
        ]
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.post(url).status_code, 404)

    def test_outsider_nao_envia_chunks(self):
        self.client.force_authenticate(self.outsider)
        chunk = SimpleUploadedFile("c.part", b"data")

        res = self.client.post(
            "/experiment/upload-chunk/",
            {
                "fileId": self.experiment.id,
                "chunkIndex": 0,
                "chunk": chunk,
            },
        )
        self.assertEqual(res.status_code, 404)

        chunk = SimpleUploadedFile("c.part", b"data")
        res = self.client.post(
            "/experiment/files/upload-chunk/",
            {"fileId": self.upload.id, "chunkIndex": 0, "chunk": chunk},
        )
        self.assertEqual(res.status_code, 404)

    def test_outsider_404_no_download_e_detalhe(self):
        self.client.force_authenticate(self.outsider)
        self.assertEqual(
            self.client.get(f"/experiment/{self.experiment.id}/").status_code, 404
        )
        self.assertEqual(
            self.client.get(f"/experiment/{self.experiment.id}/download").status_code,
            404,
        )

    def test_anonimo_recebe_401(self):
        res = self.client.get(f"/experiment/file/{self.file_data.id}/headers")
        self.assertEqual(res.status_code, 401)

    def test_dono_segue_acessando(self):
        self.client.force_authenticate(self.owner)
        res = self.client.get(f"/experiment/file/{self.file_data.id}/headers")
        self.assertEqual(res.status_code, 200)


class ExperimentDeleteTestCase(TestCase):
    """ADR-0005: DELETE /experiment/<id>/ inativa, nunca apaga a linha."""

    def setUp(self):
        self.owner = User.objects.create_user(
            username="dono", email="dono@pandora.test", password="senha-forte-123"
        )
        self.member = User.objects.create_user(
            username="membro", email="membro@pandora.test", password="senha-forte-123"
        )
        self.outsider = User.objects.create_user(
            username="fora", email="fora@pandora.test", password="senha-forte-123"
        )
        self.org = Organization.objects.create(name="Lab", org_type="lab")
        role_member = Role.objects.create(name=Role.MEMBER)
        Membership.objects.create(
            user=self.member,
            organization=self.org,
            role=role_member,
            status="active",
        )
        self.experiment = ExperimentModel.objects.create(
            title="exp", type="tipo", created_by=self.owner, organization=self.org
        )
        self.upload = FileModel.objects.create(
            file_name="a.zip", experiment=self.experiment
        )
        self.file_data = FileDataModel.objects.create(
            headers={},
            experiment=self.experiment,
            file_name="a1.fcs",
            source_path="a1.fcs",
            file=self.upload,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.owner)

    def test_delete_inativa_em_vez_de_apagar(self):
        res = self.client.delete(f"/experiment/{self.experiment.id}/")

        self.assertEqual(res.status_code, 204)
        self.experiment.refresh_from_db()
        self.assertFalse(self.experiment.active)
        self.assertTrue(ExperimentModel.objects.filter(id=self.experiment.id).exists())

    def test_inativo_sai_da_listagem_mas_volta_com_include_inactive(self):
        self.experiment.active = False
        self.experiment.save(update_fields=["active"])

        res = self.client.get("/experiment/")
        ids = [e["id"] for e in res.data]
        self.assertNotIn(self.experiment.id, ids)

        res = self.client.get("/experiment/?include_inactive=true")
        ids = [e["id"] for e in res.data]
        self.assertIn(self.experiment.id, ids)

    def test_amostras_de_experimento_inativo_ficam_invisiveis(self):
        self.experiment.active = False
        self.experiment.save(update_fields=["active"])

        res = self.client.get(f"/experiment/file/{self.file_data.id}/headers")
        self.assertEqual(res.status_code, 404)
        res = self.client.get(f"/experiment/list/data/{self.experiment.id}/")
        self.assertEqual(res.data, [])

    def test_membro_sem_papel_admin_nao_inativa(self):
        # O membro enxerga o experimento do lab, mas inativar exige dono/admin.
        self.client.force_authenticate(self.member)
        res = self.client.delete(f"/experiment/{self.experiment.id}/")

        self.assertEqual(res.status_code, 403)
        self.experiment.refresh_from_db()
        self.assertTrue(self.experiment.active)

    def test_outsider_nao_alcanca_o_delete(self):
        self.client.force_authenticate(self.outsider)
        res = self.client.delete(f"/experiment/{self.experiment.id}/")

        self.assertEqual(res.status_code, 404)


class ExperimentRestoreTestCase(TestCase):
    """POST /experiment/<id>/restore é o caminho de volta do soft-delete."""

    def setUp(self):
        self.owner = User.objects.create_user(
            username="dono", email="dono@pandora.test", password="senha-forte-123"
        )
        self.member = User.objects.create_user(
            username="membro", email="membro@pandora.test", password="senha-forte-123"
        )
        self.outsider = User.objects.create_user(
            username="fora", email="fora@pandora.test", password="senha-forte-123"
        )
        self.org = Organization.objects.create(name="Lab", org_type="lab")
        role_member = Role.objects.create(name=Role.MEMBER)
        Membership.objects.create(
            user=self.member,
            organization=self.org,
            role=role_member,
            status="active",
        )
        self.experiment = ExperimentModel.objects.create(
            title="exp",
            type="tipo",
            created_by=self.owner,
            organization=self.org,
            active=False,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.owner)

    def test_restore_reativa_e_devolve_para_a_listagem(self):
        res = self.client.post(f"/experiment/{self.experiment.id}/restore")

        self.assertEqual(res.status_code, 200)
        self.experiment.refresh_from_db()
        self.assertTrue(self.experiment.active)
        self.assertTrue(res.data["active"])

        res = self.client.get("/experiment/")
        ids = [e["id"] for e in res.data]
        self.assertIn(self.experiment.id, ids)

    def test_restore_em_ativo_e_idempotente(self):
        self.experiment.active = True
        self.experiment.save(update_fields=["active"])

        res = self.client.post(f"/experiment/{self.experiment.id}/restore")

        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data["active"])

    def test_membro_sem_papel_admin_nao_reativa(self):
        self.client.force_authenticate(self.member)
        res = self.client.post(f"/experiment/{self.experiment.id}/restore")

        self.assertEqual(res.status_code, 403)
        self.experiment.refresh_from_db()
        self.assertFalse(self.experiment.active)

    def test_outsider_recebe_404_no_restore(self):
        self.client.force_authenticate(self.outsider)
        res = self.client.post(f"/experiment/{self.experiment.id}/restore")

        self.assertEqual(res.status_code, 404)

    def test_restore_com_titulo_ativo_duplicado_retorna_409(self):
        # BE-16: reativar sobre título já usado por ativo é conflito, não 500.
        ExperimentModel.objects.create(
            title="exp",
            type="tipo",
            created_by=self.owner,
            organization=self.org,
        )

        res = self.client.post(f"/experiment/{self.experiment.id}/restore")

        self.assertEqual(res.status_code, 409)
        self.experiment.refresh_from_db()
        self.assertFalse(self.experiment.active)


class ExperimentListCreatedByNameTestCase(TestCase):
    """BE-17: a listagem expõe o username do criador para o card do front."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="ana.souza",
            email="ana@pandora.test",
            password="senha-forte-123",
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_listagem_devolve_username_do_criador(self):
        ExperimentModel.objects.create(title="exp", type="tipo", created_by=self.user)

        res = self.client.get("/experiment/")

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data[0]["created_by_name"], "ana.souza")

    def test_experimento_sem_criador_devolve_none(self):
        # Super admin enxerga experimentos sem criador (ex.: legados/SET_NULL).
        self.user.is_superuser = True
        self.user.save(update_fields=["is_superuser"])
        ExperimentModel.objects.create(title="exp", type="tipo")

        res = self.client.get("/experiment/")

        self.assertEqual(res.status_code, 200)
        self.assertIsNone(res.data[0]["created_by_name"])


class ExperimentListMetaTestCase(TestCase):
    """BE-21: my_role, progress e preview_available na listagem."""

    def setUp(self):
        self.owner = User.objects.create_user(
            username="dono", email="dono@pandora.test", password="senha-forte-123"
        )
        self.org = Organization.objects.create(name="Lab X", org_type="lab")
        self.member_role = Role.objects.create(name="member")

        self.personal = ExperimentModel.objects.create(
            title="pessoal", type="t", created_by=self.owner, status="done"
        )
        self.org_exp = ExperimentModel.objects.create(
            title="org",
            type="t",
            organization=self.org,
            status="uploading",
            total_chunks=4,
            received_chunks=[1, 2],
        )
        Membership.objects.create(
            user=self.owner,
            organization=self.org,
            role=self.member_role,
            status="active",
        )
        self.client = APIClient()
        self.client.force_authenticate(self.owner)

    def test_my_role_owner_no_pessoal_e_membership_na_org(self):
        res = self.client.get("/experiment/")

        self.assertEqual(res.status_code, 200)
        by_id = {e["id"]: e for e in res.data}
        self.assertEqual(by_id[self.personal.id]["my_role"], "owner")
        self.assertEqual(by_id[self.org_exp.id]["my_role"], "member")

    def test_progress_reflete_chunks_do_upload(self):
        res = self.client.get("/experiment/")

        by_id = {e["id"]: e for e in res.data}
        self.assertEqual(by_id[self.org_exp.id]["progress"], 50)
        # Fora de `uploading` (ou sem total) o progresso não se aplica.
        self.assertIsNone(by_id[self.personal.id]["progress"])

    def test_preview_available_com_amostra_ativa(self):
        file_model = FileModel.objects.create(
            file_name="upload.zip",
            file="upload.zip",
            sha256="b" * 64,
            experiment=self.personal,
        )
        FileDataModel.objects.create(
            headers={},
            experiment=self.personal,
            file_name="a1.fcs",
            file=file_model,
        )

        res = self.client.get("/experiment/")

        by_id = {e["id"]: e for e in res.data}
        self.assertTrue(by_id[self.personal.id]["preview_available"])
        self.assertFalse(by_id[self.org_exp.id]["preview_available"])


class ExperimentPreviewTestCase(TestCase):
    """BE-21: GET /experiment/<id>/preview — histograma 2D de baixa resolução."""

    def setUp(self):
        self.owner = User.objects.create_user(
            username="dono", email="dono@pandora.test", password="senha-forte-123"
        )
        self.stranger = User.objects.create_user(
            username="outro", email="outro@pandora.test", password="senha-forte-123"
        )
        self.experiment = ExperimentModel.objects.create(
            title="exp",
            type="t",
            created_by=self.owner,
            status="done",
            values=["FSC-A", "SSC-A"],
        )
        self.file_model = FileModel.objects.create(
            file_name="upload.zip",
            file="upload.zip",
            sha256="c" * 64,
            experiment=self.experiment,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.owner)

    def _file_data(self, **kwargs):
        return FileDataModel.objects.create(
            headers={},
            experiment=self.experiment,
            file_name="a1.fcs",
            file=self.file_model,
            **kwargs,
        )

    def test_preview_devolve_histograma_2d(self):
        self._file_data(
            data_set={
                "FSC-A": [1, 2, 3, 4, 5] * 20,
                "SSC-A": [5, 4, 3, 2, 1] * 20,
            }
        )

        res = self.client.get(f"/experiment/{self.experiment.id}/preview")

        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.data["histogram"]), 48)
        self.assertEqual(res.data["x_label"], "FSC-A")
        self.assertEqual(
            res.data["file_data_id"], self.experiment.filedatamodel_set.get().id
        )

    def test_preview_204_enquanto_nao_processado(self):
        self.experiment.status = "processing"
        self.experiment.save(update_fields=["status"])
        self._file_data(data_set={"FSC-A": [1], "SSC-A": [2]})

        res = self.client.get(f"/experiment/{self.experiment.id}/preview")

        self.assertEqual(res.status_code, 204)

    def test_preview_404_sem_amostra_ativa(self):
        self._file_data(active=False, data_set={"FSC-A": [1], "SSC-A": [2]})

        res = self.client.get(f"/experiment/{self.experiment.id}/preview")

        self.assertEqual(res.status_code, 404)

    def test_preview_escopado_ao_usuario(self):
        self._file_data(data_set={"FSC-A": [1], "SSC-A": [2]})
        self.client.force_authenticate(self.stranger)

        res = self.client.get(f"/experiment/{self.experiment.id}/preview")

        self.assertEqual(res.status_code, 404)


class CompensationDetectionTestCase(TestCase):
    """BE-22 v1: sinalização de compensação embutida nos headers FCS."""

    def setUp(self):
        self.owner = User.objects.create_user(
            username="dono", email="dono@pandora.test", password="senha-forte-123"
        )
        self.experiment = ExperimentModel.objects.create(
            title="exp", type="t", created_by=self.owner, status="done"
        )
        self.file_model = FileModel.objects.create(
            file_name="upload.zip",
            file="upload.zip",
            sha256="d" * 64,
            experiment=self.experiment,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.owner)

    def _file_data(self, headers):
        return FileDataModel.objects.create(
            headers=headers,
            experiment=self.experiment,
            file_name="a1.fcs",
            file=self.file_model,
        )

    def test_parse_spillover_formato_fcs3(self):
        from fcs_parser.services.compensation import parse_spillover

        # "n,ch1,...,chn,v11..vnn" row-major: 2 canais, identidade com
        # 12% de FITC vazando no PE.
        parsed = parse_spillover({"$SPILLOVER": "2,FITC-A,PE-A,1,0.12,0.03,1"})

        self.assertEqual(parsed["channels"], ["FITC-A", "PE-A"])
        self.assertEqual(parsed["matrix"], [[1.0, 0.12], [0.03, 1.0]])

    def test_parse_spillover_malformado_devolve_none(self):
        from fcs_parser.services.compensation import parse_spillover

        self.assertIsNone(parse_spillover(None))
        self.assertIsNone(parse_spillover({}))
        self.assertIsNone(parse_spillover({"$SPILLOVER": "abc"}))
        self.assertIsNone(parse_spillover({"$SPILLOVER": "2,FITC,PE,1,0.1"}))

    def test_listagem_sinaliza_compensated(self):
        self._file_data({"$SPILLOVER": "2,FITC-A,PE-A,1,0.12,0.03,1"})

        res = self.client.get("/experiment/")

        by_id = {e["id"]: e for e in res.data}
        self.assertTrue(by_id[self.experiment.id]["compensated"])

    def test_listagem_sem_spillover_nao_compensado(self):
        self._file_data({"$PAR": "4"})

        res = self.client.get("/experiment/")

        by_id = {e["id"]: e for e in res.data}
        self.assertFalse(by_id[self.experiment.id]["compensated"])

    def test_embedded_devolve_matriz(self):
        fd = self._file_data({"$SPILLOVER": "2,FITC-A,PE-A,1,0.12,0.03,1"})

        res = self.client.get(
            f"/experiment/{self.experiment.id}/compensations/embedded"
        )

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["channels"], ["FITC-A", "PE-A"])
        self.assertEqual(res.data["file_data_id"], fd.id)
        self.assertEqual(res.data["source"], "fcs_header")

    def test_embedded_204_sem_matriz(self):
        self._file_data({"$PAR": "4"})

        res = self.client.get(
            f"/experiment/{self.experiment.id}/compensations/embedded"
        )

        self.assertEqual(res.status_code, 204)


class CompensationControlsTestCase(TestCase):
    """BE-22: subsamples como controles + endpoints de matriz (ADR-0019)."""

    def setUp(self):
        self.owner = User.objects.create_user(
            username="dono", email="dono@pandora.test", password="senha-forte-123"
        )
        self.experiment = ExperimentModel.objects.create(
            title="exp",
            type="t",
            created_by=self.owner,
            status="done",
            values=["FSC-A", "SSC-A", "FITC-A", "PE-A"],
        )
        self.file_model = FileModel.objects.create(
            file_name="upload.zip",
            file="upload.zip",
            sha256="e" * 64,
            experiment=self.experiment,
        )
        self.subsample = SubsampleModel.objects.create(
            experiment=self.experiment, name="controles"
        )
        self.client = APIClient()
        self.client.force_authenticate(self.owner)

    def _file(self, name, data):
        return FileDataModel.objects.create(
            headers={},
            experiment=self.experiment,
            file_name=name,
            file=self.file_model,
            data_set=data,
        )

    def _mark(self, subsample, **fields):
        return self.client.patch(
            f"/experiment/{self.experiment.id}/subsamples/{subsample.id}/",
            fields,
            format="json",
        )

    def test_single_stain_exige_canal_fluorescente(self):
        res = self._mark(
            self.subsample,
            control_type="single_stain",
            control_channel="FSC-A",
        )
        self.assertEqual(res.status_code, 400)

        res = self._mark(
            self.subsample,
            control_type="single_stain",
            control_channel="FITC-A",
        )
        self.assertEqual(res.status_code, 200)

    def test_canal_de_controle_e_exclusivo(self):
        self._mark(
            self.subsample,
            control_type="single_stain",
            control_channel="FITC-A",
        )
        outro = SubsampleModel.objects.create(experiment=self.experiment, name="outro")
        res = self._mark(outro, control_type="single_stain", control_channel="FITC-A")
        self.assertEqual(res.status_code, 400)

    def test_unstained_unico_por_experimento(self):
        self._mark(self.subsample, control_type="unstained")
        outro = SubsampleModel.objects.create(experiment=self.experiment, name="neg2")
        res = self._mark(outro, control_type="unstained")
        self.assertEqual(res.status_code, 400)

    def test_compute_deriva_dos_subsamples_marcados(self):
        # neg: basal 10/10; FITC: 1000/130 (12% spill em PE); PE: 40/1000.
        neg = self._file("neg.fcs", {"FITC-A": [10, 10, 10], "PE-A": [10, 10, 10]})
        fitc = self._file(
            "fitc.fcs", {"FITC-A": [1000, 1000, 1000], "PE-A": [130, 130, 130]}
        )
        pe = self._file("pe.fcs", {"FITC-A": [40, 40, 40], "PE-A": [1000, 1000, 1000]})
        sub_neg = self.subsample
        sub_fitc = SubsampleModel.objects.create(
            experiment=self.experiment, name="fitc"
        )
        sub_pe = SubsampleModel.objects.create(experiment=self.experiment, name="pe")
        self._mark(sub_neg, control_type="unstained")
        self._mark(sub_fitc, control_type="single_stain", control_channel="FITC-A")
        self._mark(sub_pe, control_type="single_stain", control_channel="PE-A")
        neg.subsample = sub_neg
        neg.save(update_fields=["subsample"])
        fitc.subsample = sub_fitc
        fitc.save(update_fields=["subsample"])
        pe.subsample = sub_pe
        pe.save(update_fields=["subsample"])

        res = self.client.post(
            f"/experiment/{self.experiment.id}/compensations/compute",
            {},
            format="json",
        )

        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data["source"], "computed")
        self.assertEqual(res.data["channels"], ["FITC-A", "PE-A"])
        m = res.data["matrix"]
        # S[PE][FITC] = (130-10)/(1000-10) ≈ 0.1212
        self.assertAlmostEqual(m[1][0], 120 / 990, places=3)
        # S[FITC][PE] = (40-10)/(1000-10) ≈ 0.0303
        self.assertAlmostEqual(m[0][1], 30 / 990, places=3)

    def test_compute_sem_controles_da_400(self):
        res = self.client.post(
            f"/experiment/{self.experiment.id}/compensations/compute",
            {},
            format="json",
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn("negativo", res.data["detail"])

    def test_compute_com_overrides(self):
        neg = self._file("neg.fcs", {"FITC-A": [10], "PE-A": [10]})
        fitc = self._file("fitc.fcs", {"FITC-A": [1000], "PE-A": [130]})
        pe = self._file("pe.fcs", {"FITC-A": [40], "PE-A": [1000]})

        res = self.client.post(
            f"/experiment/{self.experiment.id}/compensations/compute",
            {
                "negative": [neg.id],
                "controls": {"FITC-A": [fitc.id], "PE-A": [pe.id]},
            },
            format="json",
        )

        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data["channels"], ["FITC-A", "PE-A"])

    def test_from_header_materializa_matriz(self):
        self._file("a1.fcs", {}).headers  # sem spillover → 409 primeiro
        res = self.client.post(
            f"/experiment/{self.experiment.id}/compensations/from-header",
            {},
            format="json",
        )
        self.assertEqual(res.status_code, 409)

        FileDataModel.objects.create(
            headers={"$SPILLOVER": "2,FITC-A,PE-A,1,0.12,0.03,1"},
            experiment=self.experiment,
            file_name="a2.fcs",
            file=self.file_model,
        )
        res = self.client.post(
            f"/experiment/{self.experiment.id}/compensations/from-header",
            {},
            format="json",
        )
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data["source"], "fcs_header")

    def test_apply_remove_gravam_revisao(self):
        from analytics.models import AnalysisRevision, CompensationMatrix

        matrix = CompensationMatrix.objects.create(
            experiment=self.experiment,
            name="m1",
            channels=["FITC-A", "PE-A"],
            matrix=[[1.0, 0.12], [0.03, 1.0]],
            source=CompensationMatrix.SOURCE_FCS_HEADER,
        )
        res = self.client.post(
            f"/experiment/{self.experiment.id}/compensations/{matrix.id}/apply"
        )
        self.assertEqual(res.status_code, 200)
        matrix.refresh_from_db()
        self.assertTrue(matrix.is_applied)

        rev = AnalysisRevision.objects.filter(
            experiment=self.experiment,
            action=AnalysisRevision.ACTION_COMPENSATION_APPLY,
        ).get()
        self.assertEqual(rev.target_id, matrix.id)

        res = self.client.post(f"/experiment/{self.experiment.id}/compensations/remove")
        self.assertEqual(res.status_code, 200)
        matrix.refresh_from_db()
        self.assertFalse(matrix.is_applied)
        self.assertTrue(
            AnalysisRevision.objects.filter(
                experiment=self.experiment,
                action=AnalysisRevision.ACTION_COMPENSATION_REMOVE,
            ).exists()
        )

    def test_apply_troca_matriz_anterior(self):
        from analytics.models import CompensationMatrix

        m1 = CompensationMatrix.objects.create(
            experiment=self.experiment,
            channels=["FITC-A"],
            matrix=[[1.0]],
            source="manual",
        )
        m2 = CompensationMatrix.objects.create(
            experiment=self.experiment,
            channels=["FITC-A"],
            matrix=[[1.0]],
            source="manual",
        )
        self.client.post(
            f"/experiment/{self.experiment.id}/compensations/{m1.id}/apply"
        )
        self.client.post(
            f"/experiment/{self.experiment.id}/compensations/{m2.id}/apply"
        )
        m1.refresh_from_db()
        m2.refresh_from_db()
        self.assertFalse(m1.is_applied)
        self.assertTrue(m2.is_applied)

    def test_delete_matriz_aplicada_desliga_e_descarta(self):
        from analytics.models import CompensationMatrix

        matrix = CompensationMatrix.objects.create(
            experiment=self.experiment,
            channels=["FITC-A"],
            matrix=[[1.0]],
            source="manual",
            is_applied=True,
        )
        res = self.client.delete(f"/analytics/compensations/{matrix.id}/")
        self.assertEqual(res.status_code, 204)
        matrix.refresh_from_db()
        self.assertFalse(matrix.active)
        self.assertFalse(matrix.is_applied)

    def test_density_reflete_compensacao_aplicada(self):
        from analytics.models import CompensationMatrix

        fd = self._file(
            "a1.fcs",
            {"FITC-A": [1000, 1000], "PE-A": [130, 130]},
        )
        # Spill do canal PE no detector FITC (S[FITC][PE] = 0.5): a
        # compensação desloca FITC, que é o eixo do histograma do teste.
        matrix = CompensationMatrix.objects.create(
            experiment=self.experiment,
            channels=["FITC-A", "PE-A"],
            matrix=[[1.0, 0.5], [0.0, 1.0]],
            source="manual",
        )
        url = (
            f"/experiment/file/{fd.id}/density"
            "?x=FITC-A&y=PE-A&mode=histogram&bins=4&xscale=linear"
            "&xmin=0&xmax=2000"
        )
        raw = self.client.get(url)
        self.assertEqual(raw.status_code, 200)

        self.client.post(
            f"/experiment/{self.experiment.id}/compensations/{matrix.id}/apply"
        )
        comp = self.client.get(url)
        self.assertEqual(comp.status_code, 200)
        # Histograma compensado difere do cru (PE puxado para baixo).
        self.assertNotEqual(raw.data["counts"], comp.data["counts"])

    def test_revert_do_apply_desliga_compensacao(self):
        from analytics.models import AnalysisRevision, CompensationMatrix

        matrix = CompensationMatrix.objects.create(
            experiment=self.experiment,
            channels=["FITC-A"],
            matrix=[[1.0]],
            source="manual",
        )
        self.client.post(
            f"/experiment/{self.experiment.id}/compensations/{matrix.id}/apply"
        )
        rev = AnalysisRevision.objects.filter(
            experiment=self.experiment,
            action=AnalysisRevision.ACTION_COMPENSATION_APPLY,
        ).get()

        res = self.client.post(
            f"/analytics/history/{rev.id}/revert/", {}, format="json"
        )

        self.assertEqual(res.status_code, 200)
        matrix.refresh_from_db()
        self.assertFalse(matrix.is_applied)

    def test_revert_do_remove_religa_compensacao(self):
        from analytics.models import AnalysisRevision, CompensationMatrix

        matrix = CompensationMatrix.objects.create(
            experiment=self.experiment,
            channels=["FITC-A"],
            matrix=[[1.0]],
            source="manual",
            is_applied=True,
        )
        self.client.post(f"/experiment/{self.experiment.id}/compensations/remove")
        rev = AnalysisRevision.objects.filter(
            experiment=self.experiment,
            action=AnalysisRevision.ACTION_COMPENSATION_REMOVE,
        ).get()

        res = self.client.post(
            f"/analytics/history/{rev.id}/revert/", {}, format="json"
        )

        self.assertEqual(res.status_code, 200)
        matrix.refresh_from_db()
        self.assertTrue(matrix.is_applied)

    def test_file_list_sinaliza_compensacao_embutida(self):
        fd = self._file("a1.fcs", {})
        fd.headers = {"$SPILLOVER": "2,FITC-A,PE-A,1,0.12,0.03,1"}
        fd.save(update_fields=["headers"])

        res = self.client.get(
            f"/experiment/list/data/{self.experiment.id}/"
        )

        self.assertEqual(res.status_code, 200)
        entry = next(f for f in res.data if f["id"] == fd.id)
        self.assertTrue(entry["has_embedded_compensation"])

    def test_from_header_com_apply_ja_ativa(self):
        FileDataModel.objects.create(
            headers={"$SPILLOVER": "2,FITC-A,PE-A,1,0.12,0.03,1"},
            experiment=self.experiment,
            file_name="a2.fcs",
            file=self.file_model,
        )

        res = self.client.post(
            f"/experiment/{self.experiment.id}/compensations/from-header",
            {"apply": True},
            format="json",
        )

        self.assertEqual(res.status_code, 201)
        self.assertTrue(res.data["is_applied"])
