
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

import app.main as main

from app.services.v2_prediction import predict_v2_single


class TestPredictionApiRouting(unittest.TestCase):

    def setUp(self):
        self.saved = {}
        self.model_version = SimpleNamespace(
            id=uuid.uuid4(),
            version="v2_23class_prototype",
        )
        self.confirmation = {
            "confirmed_codes": ["cough"],
            "model_features": ["E_91"],
            "has_red_flag": False,
            "red_flag_symptoms": [],
        }

        main.app.dependency_overrides[main.get_db] = (
            lambda: object()
        )
        main.app.dependency_overrides[
            main.get_current_user
        ] = lambda: SimpleNamespace(
            id=uuid.uuid4(),
            role="patient",
        )

        self.patchers = [
            patch.object(
                main,
                "confirm_symptoms",
                return_value=self.confirmation,
            ),
            patch.object(
                main,
                "get_active_model_version",
                return_value=self.model_version,
            ),
            patch.object(
                main,
                "save_prediction_session",
                side_effect=self.save_session,
            ),
            patch.object(
                main,
                "predict_v2_single",
                return_value={
                    "model_version": "v2_23class_prototype",
                    "feature_count": 66,
                    "predictions": [{
                        "rank": 1,
                        "condition": "Pneumonia",
                        "probability": 0.65,
                        "confidence_percent": 65.0,
                    }],
                    "uncertain": False,
                    "uncertainty_reasons": [],
                    "uncertainty_message": (
                        "Not a medical diagnosis."
                    ),
                    "score_note": (
                        "Uncalibrated model score."
                    ),
                },
            ),
            patch.object(
                main,
                "predict_top_conditions",
                return_value={
                    "feature_count": 198,
                    "predictions": [{
                        "rank": 1,
                        "condition": "Pneumonia",
                        "probability": 0.65,
                        "confidence_percent": 65.0,
                    }],
                },
            ),
        ]

        self.patchers.append(patch.object(main, "apply_disease_information", side_effect=lambda predictions, db: predictions))
        self.mocks = [
            patcher.start()
            for patcher in self.patchers
        ]

        self.client = TestClient(main.app)

    def tearDown(self):
        self.client.close()
        for patcher in reversed(self.patchers):
            patcher.stop()
        main.app.dependency_overrides.clear()

    def save_session(self, db, **kwargs):
        self.saved.update(kwargs)
        return SimpleNamespace(id=uuid.uuid4())

    def request_prediction(self):
        return self.client.post(
            "/predict",
            json={
                "age": 25,
                "sex": "M",
                "text": "I have a cough",
                "confirmed_codes": ["cough"],
            },
        )

    def test_v2_returns_one_condition_and_uncertainty(self):
        response = self.request_prediction()

        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertEqual(
            data["model_version"],
            "v2_23class_prototype",
        )
        self.assertEqual(data["feature_count"], 66)
        self.assertEqual(len(data["predictions"]), 1)
        self.assertIn("uncertain", data)
        self.assertIn("uncertainty_message", data)
        self.assertEqual(
            self.saved["model_version_id"],
            self.model_version.id,
        )

    def test_v1_still_returns_one_condition(self):
        self.model_version.version = "1.0"

        response = self.request_prediction()

        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertEqual(data["model_version"], "1.0")
        self.assertEqual(data["feature_count"], 198)
        self.assertEqual(len(data["predictions"]), 1)
        self.assertNotIn("uncertain", data)

    def test_unsupported_only_rejected(self):
        self.confirmation["model_features"] = [
            "E_55_@_V_101"
        ]

        response = self.request_prediction()

        self.assertEqual(response.status_code, 400)
        self.assertIn(
            "not enough V2-supported",
            response.json()["detail"],
        )
        self.assertEqual(self.saved, {})

    def test_red_flag_bypasses_model(self):
        self.confirmation["has_red_flag"] = True
        self.confirmation["red_flag_symptoms"] = [{
            "code": "coughing_blood"
        }]

        response = self.request_prediction()

        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertEqual(data["status"], "urgent_review")
        self.assertEqual(data["predictions"], [])
        self.assertIsNone(
            self.saved["model_version_id"]
        )
        self.mocks[3].assert_not_called()
        self.mocks[4].assert_not_called()

    def test_real_v2_model_through_api(self):
        # Use the real V2 predictor, not the mocked result.
        # Database access and prediction history remain mocked.
        self.mocks[3].side_effect = predict_v2_single

        response = self.request_prediction()

        self.assertEqual(response.status_code, 200)

        data = response.json()

        self.assertEqual(
            data["model_version"],
            "v2_23class_prototype",
        )
        self.assertEqual(data["feature_count"], 66)
        self.assertEqual(len(data["predictions"]), 1)

        self.assertEqual(
            data["predictions"][0]["rank"],
            1,
        )

        self.assertIn("uncertain", data)
        self.assertIn("uncertainty_message", data)
        self.assertIn("score_note", data)

        self.assertEqual(
            self.saved["model_version_id"],
            self.model_version.id,
        )
        self.assertEqual(
            len(self.saved["predictions"]),
            1,
        )

        print(
            "\nReal V2 condition:",
            data["predictions"][0]["condition"],
        )
        print(
            "Uncertain:",
            data["uncertain"],
        )


if __name__ == "__main__":
    unittest.main()
