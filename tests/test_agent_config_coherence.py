"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""

"""
Coherence checks between an agent config's prompt and its actual action space.

An agent yaml can advertise an action in ``prompt_txt.action_prompt`` that the
runtime cannot execute: the action has to survive ``flexible_parser`` *and* be
present in the ``HighLevelActionSet`` built from ``custom_actions``. When it
isn't, the model burns a step per attempt and the episode silently scores 0.
That is how ``wait()`` shipped in the UI-TARS-derived configs -- advertised in
the prompt, absent from ``custom_actions``, and not remapped by the parser.
"""

import hydra
import pytest
from hydra import compose, initialize

from open_apps.agent.utils import flexible_parser


def agent_args(agent_name: str):
    with initialize(version_base=None, config_path="../config/"):
        config = compose(config_name="config", overrides=[f"agent={agent_name}"])
    return hydra.utils.instantiate(config.agent)


def executable_actions(args) -> set[str]:
    """Action names the built HighLevelActionSet can actually run."""
    action_set = args.make_flags().action.action_set.make_action_set()
    return set(action_set.action_set.keys())


def parsed_action(response: str) -> str:
    """Run a model response through the agent's parsing path."""
    return flexible_parser(response)["action"]


GEMMA_SOM = "gemma-4-31B-computer-use"


class TestGemmaSomConfig:
    """The screenshot-only + set-of-marks Gemma config."""

    @pytest.fixture(scope="class")
    def args(self):
        return agent_args(GEMMA_SOM)

    def test_observation_is_screenshot_only_with_marks(self, args):
        assert args.use_screenshot
        assert args.save_som, "bid actions need the annotated screenshot"
        assert not args.use_axtree
        assert not args.use_html
        # save_som is what actually reaches the prompt builder as use_som.
        assert args.make_flags().obs.use_som

    def test_action_space_is_bid_based(self, args):
        assert executable_actions(args) == {
            "click",
            "fill",
            "select_option",
            "scroll",
            "noop",
        }

    def test_no_coordinate_actions_are_reachable(self, args):
        """Gemma is not prompted to ground in pixels, so don't offer it."""
        assert not {"mouse_click", "mouse_dblclick"} & executable_actions(args)

    def test_temperature_is_deterministic(self, args):
        assert args.temperature == 0

    @pytest.mark.parametrize(
        "response,expected",
        [
            ('<think>t</think><action>click("96")</action>', 'click("96")'),
            (
                '<think>t</think><action>fill("92", "a\nb")</action>',
                'fill("92", "a\nb")',
            ),
            (
                '<think>t</think><action>select_option("12", "python")</action>',
                'select_option("12", "python")',
            ),
            # scroll( is a prefix the UI-TARS remap also matches; it must fall
            # through untouched when there is no direction= kwarg.
            ("<think>t</think><action>scroll(0, 400)</action>", "scroll(0, 400)"),
            (
                "<think>t</think><action>noop(wait_ms=5000)</action>",
                "noop(wait_ms=5000)",
            ),
        ],
    )
    def test_parser_passes_bid_actions_through_unmangled(self, response, expected):
        assert parsed_action(response) == expected

    def test_every_advertised_action_is_executable(self, args):
        """The prompt must not name an action the action set cannot run."""
        prompt = args.prompt_txt.action_prompt
        runnable = executable_actions(args)
        advertised = {
            line.split("(")[0].strip()
            for line in prompt.splitlines()
            if "(" in line and not line.startswith(" ") and not line.startswith("-")
        }
        assert advertised, "could not extract any action names from action_prompt"
        assert advertised <= runnable, (
            f"action_prompt advertises {sorted(advertised - runnable)}, which "
            f"the action set cannot execute (runnable: {sorted(runnable)})"
        )

    def test_wait_is_not_advertised(self, args):
        """``wait()`` is neither remapped by the parser nor in action_map."""
        assert "wait()" not in args.prompt_txt.action_prompt

    def test_prompt_does_not_ask_for_coordinates(self, args):
        """Mixed guidance (bids + coordinates) is what produced 0% before."""
        texts = [
            args.prompt_txt.action_prompt,
            args.prompt_txt.action_abstract_example,
            args.prompt_txt.action_concrete_example,
            args.prompt_txt.think_abstract_example,
        ]
        for text in texts:
            assert "coordinate" not in str(text).lower()


class TestScreenshotEnvArgs:
    def test_screenshot_preset_widens_the_step_budget(self):
        with initialize(version_base=None, config_path="../config/"):
            config = compose(
                config_name="config", overrides=["browsergym_env_args=screenshot"]
            )
        env = config.browsergym_env_args
        assert env.max_steps == 25
        assert list(env.task_kwargs.screen_resolution) == [1280, 800]
