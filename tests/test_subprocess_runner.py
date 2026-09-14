from pathlib import Path

from databricks_local_ci.subprocess_runner import run_entrypoint


def test_run_entrypoint_captures_stdout_and_args(tmp_path: Path):
    (tmp_path / "dummy_entry.py").write_text(
        "import sys\n"
        "print('hello from dummy entrypoint')\n"
        "print(sys.argv[1:])\n"
        "sys.exit(0)\n"
    )

    result = run_entrypoint("dummy_entry", args=["--foo", "bar"], cwd=tmp_path)

    assert result.returncode == 0
    assert "hello from dummy entrypoint" in result.stdout
    assert "['--foo', 'bar']" in result.stdout


def test_run_entrypoint_captures_nonzero_exit_code(tmp_path: Path):
    (tmp_path / "dummy_fail.py").write_text("import sys\nsys.exit(3)\n")

    result = run_entrypoint("dummy_fail", cwd=tmp_path)

    assert result.returncode == 3
