Running from cluster login node:

```shell
uvx --python 3.11 vllm serve google/gemma-4-E2B-it \
  --download-dir /checkpoint/memorization/hf_cache \
  --tensor-parallel-size 1 \
  --dtype bfloat16 \
  --max-model-len 8192 \
  --port 8000
```

See my running jobs in the cluster:

`squeue --me` (ie):

```shell
JOBID PARTITION     NAME     USER ST       TIME  NODES NODELIST(REASON)
9745069      h200     bash aaronsmu  R    1:22:29      1 h200-002-234
```

Show node IP:

`scontrol show node h200-002-234 | grep -oP 'NodeAddr=\K\S+'`

Jump inside the job and check if model is running:

`srun --overlap --jobid=<JOBID> --pty curl -s http://localhost:8000/v1/models'`
