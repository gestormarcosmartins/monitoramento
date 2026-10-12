import unittest

from fastapi import HTTPException
from fastapi.testclient import TestClient

from central_monitor.main import app


@app.get("/__tests/error-contract/http")
def _test_http_error() -> None:
    raise HTTPException(status_code=409, detail="Version conflict")


@app.get("/__tests/error-contract/validation")
def _test_validation(required_value: int) -> dict[str, int]:
    return {"required_value": required_value}


class HttpErrorContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app, raise_server_exceptions=False)

    def tearDown(self) -> None:
        self.client.close()

    def test_http_errors_have_stable_code_correlation_and_retryability(self) -> None:
        response = self.client.get(
            "/__tests/error-contract/http",
            headers={"X-Correlation-ID": "test-correlation-123"},
        )
        body = response.json()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(body["code"], "conflict")
        self.assertEqual(body["message"], "Version conflict")
        self.assertEqual(body["detail"], "Version conflict")
        self.assertEqual(body["correlation_id"], "test-correlation-123")
        self.assertFalse(body["retryable"])
        self.assertEqual(response.headers["X-Correlation-ID"], "test-correlation-123")

    def test_validation_errors_identify_invalid_fields(self) -> None:
        response = self.client.get("/__tests/error-contract/validation?required_value=not-an-integer")
        body = response.json()
        self.assertEqual(response.status_code, 422)
        self.assertEqual(body["code"], "validation_error")
        self.assertEqual(body["invalid_fields"][0]["field"], "required_value")
        self.assertTrue(body["correlation_id"])
        self.assertFalse(body["retryable"])

    def test_invalid_correlation_header_is_replaced(self) -> None:
        response = self.client.get(
            "/__tests/error-contract/http",
            headers={"X-Correlation-ID": "contains spaces and is rejected"},
        )
        self.assertNotEqual(response.json()["correlation_id"], "contains spaces and is rejected")
        self.assertEqual(response.headers["X-Correlation-ID"], response.json()["correlation_id"])

    def test_openapi_publishes_the_shared_error_envelope(self) -> None:
        schema = self.client.get("/api/openapi.json").json()
        error_schema = schema["components"]["schemas"]["ErrorResponse"]
        self.assertEqual(
            set(error_schema["required"]),
            {"code", "message", "detail", "correlation_id", "retryable"},
        )
        responses = schema["paths"]["/api/v1/assets"]["get"]["responses"]
        self.assertIn("403", responses)
        self.assertIn("503", responses)


if __name__ == "__main__":
    unittest.main()
