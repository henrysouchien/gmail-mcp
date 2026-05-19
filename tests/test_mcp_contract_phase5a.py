import ast
import importlib
import unittest
from pathlib import Path


TARGETS = {
    "gmail_create_draft": {
        "discovery": "gmail_list_inbox",
        "siblings": (
            "gmail_create_reply_draft",
            "gmail_send_draft",
            "gmail_search_emails",
            "gmail_read_email",
        ),
    },
    "gmail_create_reply_draft": {
        "discovery": "gmail_list_inbox",
        "siblings": (
            "gmail_create_draft",
            "gmail_send_draft",
            "gmail_search_emails",
            "gmail_read_email",
        ),
    },
}


def _ast_docstring(tool_name: str) -> str:
    server_path = Path(__file__).resolve().parents[1] / "src" / "server.py"
    module = ast.parse(server_path.read_text())
    for node in module.body:
        if isinstance(node, ast.FunctionDef) and node.name == tool_name:
            return ast.get_docstring(node) or ""
    raise AssertionError(f"Tool function not found: {tool_name}")


def _registered_docstring(tool_name: str) -> str:
    server = importlib.import_module("src.server")
    try:
        tool = server.mcp._tool_manager._tools[tool_name]
        return tool.fn.__doc__ or ""
    except (AttributeError, KeyError):
        return _ast_docstring(tool_name)


class Phase5aMcpContractTests(unittest.TestCase):
    def test_discovery_and_sibling_contracts(self) -> None:
        for tool_name, expected in TARGETS.items():
            with self.subTest(tool=tool_name):
                doc = _registered_docstring(tool_name)
                self.assertIn("Discovery:", doc)
                self.assertIn(expected["discovery"], doc)
                self.assertTrue(
                    any(sibling in doc for sibling in expected["siblings"]),
                    f"{tool_name} docstring does not reference any sibling tool",
                )


if __name__ == "__main__":
    unittest.main()
