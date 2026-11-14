# Deploying Siteline to EC2

One Ubuntu box runs two containers from `docker-compose.prod.yml`:
- `web`: Caddy serves the web build at `/` with automatic HTTPS, and proxies `/api/*` to agent-api, flushing server-sent events immediately.
- `agent`: agent-api with `AGENT_BACKEND=local` and `AGENT_MODE=scripted`.

Amazon Nova on Bedrock is called with the EC2 instance role. There are no AWS keys on the box or in `.env`.

## What lives where

| Item | How it gets to the box |
|---|---|
| Code | `git clone` (public repo) by `infra/ec2-bootstrap.sh` |
| `.env` | copied by hand (`scp`); no AWS keys, `AWS_REGION=us-east-1`, model ids, `DEMO_AUTO_APPROVE_SECONDS=0`, optional `LANGSMITH_*` |
| `data/raw/` (CoStar, grid, tracts), `data/derived/general_stock.parquet`, `data/golden/` | copied by hand (`rsync`); never committed, never in an image |
| `runs/` (run artifacts, LLM cache, runs.db) | created on the box |

## Manual AWS steps (us-east-1)

1. **IAM role** `siteline-ec2`, trusted by `ec2.amazonaws.com`, with this inline policy. Nova goes through cross-region `us.` inference profiles, so the policy covers the profile and the models in every region it routes to:
   ```json
   {
     "Version": "2012-10-17",
     "Statement": [{
       "Effect": "Allow",
       "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
       "Resource": [
         "arn:aws:bedrock:*::foundation-model/amazon.nova-*",
         "arn:aws:bedrock:*:<ACCOUNT_ID>:inference-profile/us.amazon.nova-*"
       ]
     }]
   }
   ```
   Create an instance profile with the same name, holding the role.
2. **Security group** `siteline-web`: inbound 22 from your IP only, and 80 and 443 from anywhere.
3. **Instance** t3.large, Ubuntu 24.04 LTS, 30 GB gp3, the role above, the security group, and your key pair.
   - Metadata: **IMDSv2 required** with hop limit 2, so the containers reach the instance role through IMDSv2 only. This is the mitigation for the residual SSRF risk in live dataset fetches.
   - User data: `infra/ec2-bootstrap.sh`.
4. **Elastic IP**, associated with the instance.
5. **Budget alert**: Billing → Budgets → $50 monthly cost budget with an email alert at 80%.

## Deploy

```bash
IP=<elastic ip>; HOST=$(echo $IP | tr . -).sslip.io      # sslip.io resolves to the IP, so Caddy gets a real certificate
ssh ubuntu@$IP 'cloud-init status --wait'                # bootstrap finished
scp .env.prod ubuntu@$IP:siteline/.env                   # a prod .env without AWS keys
rsync -av data/raw data/derived data/golden ubuntu@$IP:siteline/data/
ssh ubuntu@$IP "cd siteline && SITE_ADDRESS=$HOST docker compose -f docker-compose.prod.yml up -d --build"
curl https://$HOST/api/health && curl https://$HOST/api/golden
PYTHONPATH=. python scripts/e2e.py --replay-only --golden laundromat --agent-url https://$HOST/api
```

Update: `ssh ubuntu@$IP "cd siteline && git pull && SITE_ADDRESS=$HOST docker compose -f docker-compose.prod.yml up -d --build"`.

Golden runs: record on a laptop with `scripts/record_golden.py <run_id> <name>` and `rsync data/golden` again; no restart is needed.

## Deploy checklist

- [ ] Role, instance profile, security group, IMDSv2 required (hop limit 2), Elastic IP, $50 budget alert
- [ ] `.env` on the box has no AWS keys; `ssh ubuntu@$IP 'cd siteline && docker compose -f docker-compose.prod.yml exec agent python scripts/check_bedrock.py'` reaches Nova
- [ ] `https://$HOST` loads the map; `/api/golden` lists `laundromat` and `orthopedic_group`
- [ ] Golden replay passes against `https://$HOST/api` (the demo safety net)
- [ ] One live laundromat run from the browser reaches a verified top 10
- [ ] After the hackathon: stop or terminate the instance, release the Elastic IP, and delete the access key pasted in chat
