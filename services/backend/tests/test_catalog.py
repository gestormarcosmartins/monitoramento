import unittest

from fastapi import HTTPException

from central_monitor.catalog import FORBIDDEN_ATTACHMENT_TYPES, canonical_hash, detected_type, eligible, safe_attachment_name, validate_answers, validate_form_schema


class CatalogRulesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.schema = {"fields": [
            {"code": "summary", "type": "text", "required": True},
            {"code": "quantity", "type": "number"},
            {"code": "systems", "type": "multi_select", "options": ["ERP", "CRM"]},
        ]}

    def test_schema_rejects_executable_conditions(self) -> None:
        with self.assertRaises(ValueError):
            validate_form_schema({"fields": [{"code": "x", "type": "text", "visible_when": {"operator": "javascript"}}]})

    def test_answers_are_validated_against_published_schema(self) -> None:
        validate_answers(self.schema, {"summary": "Acesso", "quantity": 2, "systems": ["ERP"]})
        with self.assertRaises(HTTPException):
            validate_answers(self.schema, {"quantity": 2})
        with self.assertRaises(HTTPException):
            validate_answers(self.schema, {"summary": "Acesso", "systems": ["Inexistente"]})

    def test_snapshot_hash_is_stable(self) -> None:
        self.assertEqual(canonical_hash({"a": 1, "b": 2}), canonical_hash({"b": 2, "a": 1}))

    def test_eligibility_is_explicit_by_role(self) -> None:
        version = {"eligibility": {"roles": ["client"]}}
        self.assertTrue(eligible(version, "client"))
        self.assertFalse(eligible(version, "operator"))

    def test_attachment_type_uses_file_signature(self) -> None:
        self.assertEqual(detected_type(b"%PDF-1.7 content"), "application/pdf")
        self.assertEqual(detected_type(b"not a known file"), "application/octet-stream")

    def test_active_and_executable_attachments_are_blocked_by_signature(self) -> None:
        self.assertIn(detected_type(b"MZ executable"),FORBIDDEN_ATTACHMENT_TYPES)
        self.assertIn(detected_type(b"<script>alert(1)</script>"),FORBIDDEN_ATTACHMENT_TYPES)
        self.assertIn(detected_type(b"#!/bin/sh\necho unsafe"),FORBIDDEN_ATTACHMENT_TYPES)

    def test_attachment_filename_cannot_inject_headers_or_paths(self) -> None:
        self.assertEqual(safe_attachment_name("../folder/report\r\nX-Evil: yes.pdf"),"reportX-Evil: yes.pdf")


if __name__ == "__main__":
    unittest.main()
