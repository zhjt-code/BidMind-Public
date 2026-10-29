"""Metrics must handle FastAPI's included routers without breaking API requests."""

import unittest

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from prometheus_client import CollectorRegistry
from prometheus_fastapi_instrumentator import Instrumentator


class IncludedRouterMetricsTest(unittest.TestCase):
    def test_included_router_and_missing_route(self):
        app = FastAPI()
        router = APIRouter()

        @router.get("/health")
        def health():
            return {"status": "ok"}

        app.include_router(router, prefix="/api")
        Instrumentator(registry=CollectorRegistry()).instrument(app).expose(app)
        with TestClient(app) as client:
            response = client.get("/api/health")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {"status": "ok"})
            self.assertEqual(client.get("/missing").status_code, 404)
            metrics = client.get("/metrics")
            self.assertEqual(metrics.status_code, 200)
            self.assertIn('handler="/api/health"', metrics.text)
