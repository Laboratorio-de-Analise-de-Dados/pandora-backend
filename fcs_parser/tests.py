from django.core.exceptions import ValidationError
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import User
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
