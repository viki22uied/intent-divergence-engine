from pathlib import Path


def test_action_has_force_input():
    text = Path("action.yml").read_text()
    assert "force-pull-request-target:" in text


def test_run_step_uses_env_not_direct_inputs():
    text = Path("action.yml").read_text()
    # The `id: run` step's `run:` block must not contain direct ${{ inputs. splicing.
    # All inputs.* should only appear in `env:` blocks.
    run_section = text.split("id: run")[1].split("continue-on-error")[0]
    # find the run: | block
    run_block = run_section.split("run: |")[1] if "run: |" in run_section else ""
    assert "${{ inputs." not in run_block, "run: block still interpolates inputs.* directly"
    assert "FORCE_PULL_REQUEST_TARGET" in run_section


def test_post_comment_uses_env():
    text = Path("action.yml").read_text()
    post = text.split("Post PR comment")[1]
    run_block = post.split("run: |")[1].split("Enforce result")[0] if "run: |" in post else ""
    assert "${{ github.repository }}" not in run_block
    assert "${{ github.event.pull_request.number }}" not in run_block
    assert "GITHUB_REPOSITORY" in post
    assert "GITHUB_PR_NUMBER" in post
