
<div align="center">

 #  <img width="45" height="45" alt="image" src="https://github.com/user-attachments/assets/6c409d42-6f3a-4a62-be7f-57793d9dad9d" /> OpenApps
 
*Building Blocks for Computer-Use Agents Research*

🏆 ICLR Oral, Top 1%

[📒 docs](https://facebookresearch.github.io/OpenApps/)  | [📑 ArXiV](https://arxiv.org/abs/2511.20766) | [🎬 Video Tutorial](https://www.youtube.com/watch?v=gzNW_LXE7OE)
</div>



Evaluate and train multimodal agents to use apps like humans do (by clicking, typing, and scrolling):

✅ **Unlimited data** (for evaluating and training UI-agents): Configurable state and design to generate thousands of versions of each app

✅ **Lightweight**: runs on a single CPU (and Python-based); no Docker or OS emulators needed

✅ **Ground truth rewards**: task rewards are based on the underlying state and all app logic is transparent in Python


## Install

1. Clone
```
git clone https://github.com/facebookresearch/OpenApps.git
```

2. Install
```
uv sync
```

see [docs](https://facebookresearch.github.io/OpenApps/) for details.


## Run OpenApps

Simply run:

```bash
uv run launch.py 
```
<img width="1440" height="822" alt="image" src="https://github.com/user-attachments/assets/46024c36-9f6d-462b-acb7-b6c148ed1754" />


Each app can be modified with variables available in `config/apps`. You can override any of these via command line:

```bash
uv run launch.py app.todo.title='Super Todo'
```

Learn more about to customize the content and appearance of apps in the [docs](https://facebookresearch.github.io/OpenApps/). 



## Launch an Agent

For agents to directly interact with apps, install: `playwright install chromium`.

Launch an agent to perform a task of *adding a meeting with Dennis to the calendar*:


```
# export GPT55_API_KEY=""
uv run launch_agent.py agent=GPT-5.5-computer-use task_name=add_meeting_with_dennis
```

To see the agent solving the task live, add the headless argument:
```
uv run launch_agent.py ... browsergym_env_args.headless=False
```
![gif (1)](https://github.com/user-attachments/assets/cbf3c02e-0bad-4be7-8b4d-31c64fda49a0)


You can specify the agent of your choice with the `agent=` argument. For example `agent=dummy` is a simple agent that clicks randomly on any buttons, great for exploration!

Learn more about launching with OpenAI, Claude, and VLLM models such as UI-Tars in our [docs](https://facebookresearch.github.io/OpenApps/).

## Environment variables

`launch_agent.py` and `launch_parallel_agents.py` call `load_dotenv()`, so a `.env` file at
the repo root is picked up automatically (`.env` is git-ignored — keep keys out of configs):

```bash
cat > .env <<'EOF'
GPT55_API_KEY=...
WANDB_API_KEY=...
WANDB_BASE_URL=...       # only for a self-hosted W&B server
EOF
```

| Variable | Read by | Purpose |
| --- | --- | --- |
| `USER` | `config/config*.yaml`, `config/mode/*` | W&B `entity` and the `logs_dir` path |
| `GPT55_API_KEY` | `config/agent/GPT-5.5-*.yaml` | key for the OpenAI-compatible endpoint |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN` | `config/agent/claude_4_sonnet.yaml` (`client_type: aws`) | Bedrock credentials, when not set in the config |
| `WANDB_API_KEY`, `WANDB_BASE_URL`, `WANDB_MODE` | `wandb` | auth, self-hosted server, and `WANDB_MODE=offline` to skip online logging |
| `EXPERIMENT_CONFIG_PATH` | `src/open_apps/configs.py` | load a saved config YAML instead of composing the Hydra defaults |
| `OPENAPPS_APP`, `OPENAPPS_MCP_HOST`, `OPENAPPS_MCP_PORT` | `src/open_apps/mcp/` | app served by the MCP server, and its bind address |

Agent API keys are read through Hydra interpolation, so any variable name works — point the
agent's `api_key` at the one you use:

```bash
uv run launch_agent.py agent=GPT-5.5-computer-use 'agent.api_key=${oc.env:OPENAI_API_KEY}'
```

The batch scripts are configured entirely by environment:

| Variable | Script | Default |
| --- | --- | --- |
| `AGENTS` | `scripts/conduct.sh` | `dummy` — space-separated `config/agent/<name>` stems, used round-robin |
| `COUNT` | `scripts/conduct.sh` | number of agents — total runs to launch |
| `MAX_PARALLEL` | `scripts/conduct.sh` | `4` — concurrent runs |
| `HEADLESS` | `scripts/conduct.sh` | `True` |
| `LOG_DIR`, `WANDB_GROUP` | `scripts/conduct.sh` | `log_outputs`, `batch-<timestamp>` |
| `VLLM_MODEL`, `VLLM_PORT` | `scripts/conduct_slurm.sh` | the `served_model_name` and port to look for |
| `VLLM_HOST` | `scripts/conduct_slurm.sh` | unset — pin a node to skip auto-discovery |

## Running on SLURM

`config/mode/slurm_cluster.yaml` ships with placeholder values (`logs_dir: /example/dir`,
`slurm_account: example_replace_me`, …) that `sbatch` will reject. Rather than editing it and
risking committing your site's paths and account names, `.gitignore` carries an `internal-*`
rule: **any file named `internal-*` stays untracked**. The convention is to keep a private
twin next to the public one:

```bash
cp config/mode/slurm_cluster.yaml config/mode/internal-slurm_cluster.yaml
```

```yaml
# config/mode/internal-slurm_cluster.yaml  (untracked)
# @package _global_
project: open_apps

logs_dir: /your/checkpoint/path/${oc.env:USER}/logs/${project}/${now:%Y-%m-%d_%H-%M-%S}-${agent.model_name}/${job_id}
databases_dir: ${logs_dir}/databases

cluster: slurm

slurm_sweep_launcher:
  gpus_per_node: 0
  nodes: 1
  tasks_per_node: 1
  cpus_per_task: 2
  timeout_min: 400
  slurm_account: your_account
  slurm_qos: your_qos
  slurm_partition: your_partition
  mem_gb: 10
  slurm_srun_args: ["-vv", "--cpu-bind", "none"]
  slurm_comment: "parallel agent tasks"
```

Select it like any other Hydra mode:

```bash
uv run launch_parallel_agents.py mode=internal-slurm_cluster agent=dummy \
    tasks=longer_horizon parallel_tasks.task_names=all use_wandb=True
```

The same pattern applies elsewhere — e.g. `docs/internal-notes.md` for cluster-specific
instructions alongside the public `docs/`.

For a self-hosted model, serve it on a GPU node and point the agent at that host:

```bash
# on the GPU node
vllm serve <model> --host 0.0.0.0 --port 8000

# from anywhere on the cluster
uv run launch_agent.py agent=Qwen3.6-27B-computer-use agent.hostname=<node> agent.port=8000
```

`scripts/conduct_slurm.sh` automates that last step: it requests a CPU allocation, probes
your running jobs (`squeue --me`) for a node serving `$VLLM_MODEL` on `$VLLM_PORT`, and runs
the worker pool against it. Override the account/QOS/partition at submit time instead of
editing the `#SBATCH` placeholders:

```bash
AGENTS=gemma-4-computer-use COUNT=1 \
  sbatch --account=... --qos=... --partition=... scripts/conduct_slurm.sh
```

See the [agents docs](https://facebookresearch.github.io/OpenApps/agents/) for the full
cluster walkthrough.

## OpenApps in action


https://github.com/user-attachments/assets/40482d53-9481-4e48-962b-eb384e94e3c7




## Contributing

We welcome pull requests with new features or issues via GitHub.


### Development

```
uv sync --extra dev
```

To build docs:

```
mkdocs build
mkdocs serve
``` 

this will launch docs available at https://facebookresearch.github.io/OpenApps/


### Testing

Run all tests via:

```python
uv run -m pytest tests/
```




## Attribution

Our apps are built on top of several excellent frameworks:  

- FastHTML [framework](https://github.com/AnswerDotAI/fasthtml) and [examples](https://github.com/AnswerDotAI/fasthtml-example) which allowed us to build fully functional apps in Python, the language most familiar to AI researchers.
- [Browser Gym](https://github.com/ServiceNow/BrowserGym/blob/main/LICENSE) and [AgentLab](https://github.com/ServiceNow/AgentLab/blob/main/LICENSE):
- [Spacy](https://github.com/innoq/spacy/blob/main/LICENSE): for natural language processing
- Open Street Maps: https://www.openstreetmap.org/copyright for our Maps apps.
- (and for the optional webshop) we rely on [WebShop](https://github.com/princeton-nlp/WebShop/blob/master/LICENSE.md) developed by Princeton 

Some icons are have been designed using resources from Flaticon.com

Our work is licensed under CC-BY-NC, please refer to the [LICENSE](LICENSE) file in the top level directory.

Copyright © Meta Platforms, Inc. See the [Terms of Use](https://opensource.fb.com/legal/terms/) and [Privacy Policy](https://opensource.fb.com/legal/privacy/) for this project.

## Acknowledgements

* A big thank you to Taj Gillin for implementing an MCP interface for OpenApps and Jiayu Wang for suggestions to improve our harness!
* Another thank you to Yuval Kansal for fixing inconsistencies in task phrasings!

## Featured In

* BrowserGym: https://github.com/servicenow/browsergym
* NE Agents Day (🏆Oral Award) : https://ne-agents-day.github.io/
* OpenEnv (HugginFace and PyTorch RL environment):  https://huggingface.co/docs/openenv/environments/openapp

## Cite

```
@article{ullrich2025openapps0,
  title   = {OpenApps: Simulating Environment Variations to Measure UI-Agent Reliability},
  author  = {Karen Ullrich and Jingtong Su and Claudia Shi and Arjun Subramonian and Amir Bar and Ivan Evtimov and Nikolaos Tsilivis and Randall Balestriero and Julia Kempe and Mark Ibrahim},
  year    = {2025},
  journal = {arXiv preprint arXiv: 2511.20766}
}
```
