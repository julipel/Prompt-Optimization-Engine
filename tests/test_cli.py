import subprocess
import sys

import pytest

from prompt_optimizer.cli import main


def test_module_help():
    result = subprocess.run(
        [sys.executable, "-m", "prompt_optimizer", "--help"],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0
    assert "Prompt Optimization Engine" in result.stdout


def test_default_help(capsys):
    assert main([]) == 0
    assert "--version" in capsys.readouterr().out


def test_invalid_arguments():
    with pytest.raises(SystemExit) as error:
        main(["--unknown"])
    assert error.value.code == 2
