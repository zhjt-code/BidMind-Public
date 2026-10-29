"""Exercise the FastAPI/Starlette boundary used by metrics middleware."""

import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from prometheus_client import CollectorRegistry
from prometheus_fastapi_instrumentator import Instrumentator


class MetricsCompatibilityTest(unittest.TestCase):
    def test_success_and_missing_routes_are_recorded(self):
        app = FastAPI()

        @app.get("/health")
        def health():
            return {"status": "ok"}

        registry = CollectorRegistry()
        Instrumentator(registry=registry).instrument(app).expose(app)
        with TestClient(app) as client:
            self.assertEqual(client.get("/health").json(), {"status": "ok"})
            self.assertEqual(client.get("/missing").status_code, 404)
            metrics = client.get("/metrics")
            self.assertEqual(metrics.status_code, 200)
            self.assertIn('handler="/health"', metrics.text)
            self.assertIn('status="2xx"', metrics.text)
            self.assertIn('status="4xx"', metrics.text)
