from pathlib import Path

from src import gmail_client


def test_default_oauth_material_lives_outside_source_checkout() -> None:
    expected_dir = Path.home() / ".config" / "gmail-mcp"

    assert gmail_client.CONFIG_DIR == expected_dir
    assert gmail_client.CREDENTIALS_FILE == expected_dir / "gmail_credentials.json"
    assert gmail_client.TOKEN_FILE == expected_dir / "gmail_token.pickle"
