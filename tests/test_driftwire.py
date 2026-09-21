"""End-to-end tests for DriftWire: JSON Schema validator, diff, and live check."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from driftwire import check, diff, jsonschema


# --- JSON Schema validator -------------------------------------------------

def test_type_mismatch():
    f = jsonschema.validate({"type": "string"}, 42)
    assert any(x["type"] == "type_mismatch" for x in f)


def test_missing_required():
    schema = {"type": "object", "required": ["id", "name"], "properties": {"id": {"type": "integer"}}}
    f = jsonschema.validate(schema, {"id": 1})
    assert any(x["type"] == "missing_required" and "name" in x["message"] for x in f)


def test_undocumented_field():
    schema = {"type": "object", "properties": {"id": {"type": "integer"}}, "additionalProperties": False}
    f = jsonschema.validate(schema, {"id": 1, "extra": "nope"})
    assert any(x["type"] == "undocumented_field" and "extra" in x["message"] for x in f)


def test_additional_properties_true_allows_extra():
    schema = {"type": "object", "properties": {"id": {"type": "integer"}}}
    f = jsonschema.validate(schema, {"id": 1, "extra": "ok"})
    assert not f


def test_nested_path_and_pointer():
    schema = {"type": "object", "properties": {"a": {"type": "object", "properties": {"b": {"type": "string"}}}}}
    f = jsonschema.validate(schema, {"a": {"b": 5}})
    assert any(x["path"] == "/a/b" and x["type"] == "type_mismatch" for x in f)


def test_array_items():
    schema = {"type": "array", "items": {"type": "integer"}}
    f = jsonschema.validate(schema, [1, 2, "x"])
    assert any(x["path"] == "/2" and x["type"] == "type_mismatch" for x in f)


def test_ref_resolution():
    root = {
        "components": {"schemas": {"Pet": {"type": "object", "required": ["id"],
                                            "properties": {"id": {"type": "integer"}}}}}
    }
    def resolver(ref):
        return root["components"]["schemas"][ref.split("/")[-1]]
    f = jsonschema.validate({"$ref": "#/components/schemas/Pet"}, {"id": "wrong"}, resolver)
    assert any(x["type"] == "type_mismatch" for x in f)


def test_enum_violation():
    f = jsonschema.validate({"enum": ["a", "b"]}, "c")
    assert any(x["type"] == "enum_violation" for x in f)


def test_nullable():
    f = jsonschema.validate({"type": "string", "nullable": True}, None)
    assert not f


def test_valid_object_no_findings():
    schema = {"type": "object", "required": ["id"], "properties": {"id": {"type": "integer"}}}
    assert jsonschema.validate(schema, {"id": 7}) == []


# --- Spec-vs-spec diff -----------------------------------------------------

OLD_SPEC = {
    "openapi": "3.0.0",
    "paths": {
        "/users": {
            "get": {
                "parameters": [{"name": "limit", "in": "query", "required": False, "schema": {"type": "integer"}}],
                "responses": {
                    "200": {"content": {"application/json": {"schema": {
                        "type": "object", "required": ["id", "name"],
                        "properties": {"id": {"type": "integer"}, "name": {"type": "string"},
                                       "status": {"type": "string", "enum": ["active", "inactive"]}}
                    }}}}
                },
            }
        }
    },
}


def test_diff_removed_endpoint():
    new = json.loads(json.dumps(OLD_SPEC))
    del new["paths"]["/users"]
    f = diff.diff_specs(OLD_SPEC, new)
    assert any(x["type"] == "removed_endpoint" for x in f)


def test_diff_removed_field():
    new = json.loads(json.dumps(OLD_SPEC))
    schema = new["paths"]["/users"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    del schema["properties"]["name"]
    f = diff.diff_specs(OLD_SPEC, new)
    assert any(x["type"] == "removed_field" and "name" in x["message"] for x in f)


def test_diff_added_required():
    new = json.loads(json.dumps(OLD_SPEC))
    schema = new["paths"]["/users"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    schema["required"] = ["id", "name", "email"]
    f = diff.diff_specs(OLD_SPEC, new)
    assert any(x["type"] == "added_required" and "email" in x["message"] for x in f)


def test_diff_removed_enum_value():
    new = json.loads(json.dumps(OLD_SPEC))
    schema = new["paths"]["/users"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    schema["properties"]["status"]["enum"] = ["active"]
    f = diff.diff_specs(OLD_SPEC, new)
    assert any(x["type"] == "removed_enum" for x in f)


def test_diff_added_required_param():
    new = json.loads(json.dumps(OLD_SPEC))
    new["paths"]["/users"]["get"]["parameters"][0]["required"] = True
    f = diff.diff_specs(OLD_SPEC, new)
    assert any(x["type"] == "added_required_parameter" for x in f)


def test_diff_no_change():
    new = json.loads(json.dumps(OLD_SPEC))
    assert diff.diff_specs(OLD_SPEC, new) == []


# --- Spec-vs-reality live check --------------------------------------------

class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/users":
            body = {"id": 1, "name": "alice", "undeclared": True}  # undeclared + missing required? id+name present
            self._json(200, body)
        elif self.path == "/broken":
            self._json(200, {"id": "not-an-int", "name": "bob"})  # type mismatch
        else:
            self._json(404, {"error": "not found"})

    def _json(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


def _serve():
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server


def test_check_detects_type_mismatch():
    server = _serve()
    try:
        spec = {"paths": {"/broken": {"get": {"responses": {"200": {"content": {"application/json": {"schema": {
            "type": "object", "properties": {"id": {"type": "integer"}, "name": {"type": "string"}}}}}}}}}}}
        result = check.check_spec(spec, f"http://127.0.0.1:{server.server_port}")
        assert result["probed"] == 1
        assert any(x["type"] == "type_mismatch" and "/broken" in x["path"] for x in result["findings"])
    finally:
        server.shutdown()


def test_check_detects_undocumented_field():
    server = _serve()
    try:
        spec = {"paths": {"/users": {"get": {"responses": {"200": {"content": {"application/json": {"schema": {
            "type": "object", "properties": {"id": {"type": "integer"}, "name": {"type": "string"}},
            "additionalProperties": False}}}}}}}}}
        result = check.check_spec(spec, f"http://127.0.0.1:{server.server_port}")
        assert any(x["type"] == "undocumented_field" and "undeclared" in x["message"] for x in result["findings"])
    finally:
        server.shutdown()


def test_check_clean_endpoint_no_findings():
    server = _serve()
    try:
        spec = {"paths": {"/users": {"get": {"responses": {"200": {"content": {"application/json": {"schema": {
            "type": "object", "required": ["id", "name"],
            "properties": {"id": {"type": "integer"}, "name": {"type": "string"}}}}}}}}}}}
        result = check.check_spec(spec, f"http://127.0.0.1:{server.server_port}")
        assert result["findings"] == []
    finally:
        server.shutdown()


def test_check_skips_missing_path_params():
    spec = {"paths": {"/users/{id}": {"get": {"responses": {"200": {"description": "ok"}}}}}}
    result = check.check_spec(spec, "http://127.0.0.1:1")
    assert result["probed"] == 0 and result["skipped"] == 1


def test_check_path_param_filled():
    server = _serve()
    try:
        spec = {"paths": {"/users/{id}": {"get": {"responses": {"200": {"description": "ok"}}}}}}
        result = check.check_spec(spec, f"http://127.0.0.1:{server.server_port}",
                                  path_params={"id": "1"})
        # /users/1 -> handler returns 404 -> no schema for 404 -> no findings, but probed
        assert result["probed"] == 1 and result["errors"] == 0
    finally:
        server.shutdown()
