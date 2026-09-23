from pathlib import Path

from vmd.app import create_app


def test_eco_controls_have_one_handler_and_visible_feedback(tmp_path):
    root = Path(__file__).parents[1]
    main = (root / "vmd/static/main.js").read_text()
    capacity = (root / "vmd/static/capacity.js").read_text()
    assert main.count("async function setEcoMode(") == 1
    assert "role', 'switch'" in main
    assert "Saving…" in main and "showSuccess" in main
    assert "role', 'switch'" in capacity
    assert "eco-save-status" in capacity

    app = create_app(tmp_path, tmp_path)
    try:
        page = app.test_client().get("/").get_data(as_text=True)
        assert 'id="eco-mode" name="eco_mode" type="checkbox"' in page
        assert 'id="eco-mode" name="eco_mode" type="checkbox" checked' not in page
        assert 'id="eco-save-status"' in page
        assert "Eco mode compared with Normal mode" in page
        assert "Visible weapons are still checked every second" in page
        assert "20260922-depth1" in page
    finally:
        app.extensions["vmd_manager"].close()
