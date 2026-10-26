# Simple HTTPS Fleet Controller (authorized lab use)

A small, concurrent HTTPS controller and cross-platform Python agent for **inventory/status reporting and fixed, allowlisted diagnostics**. It does not provide an arbitrary remote shell or arbitrary command execution.

## Requirements
- Controller: Python 3.10+
- Targets: Python 3.10+ installed and reachable from the controller over HTTPS
- Ansible controller: Ansible Core; `ansible.windows` collection for Windows targets
- TLS certificate/key for the controller

## 1. Configure TLS and token
On the Linux controller, create a certificate for the DNS name or IP agents will use. For a quick isolated lab, a self-signed certificate can be created with:

```bash
mkdir -p certs
openssl req -x509 -newkey rsa:3072 -nodes -days 30 \
  -keyout certs/server.key -out certs/server.crt \
  -subj "/CN=c2-controller.example.test" \
  -addext "subjectAltName=DNS:c2-controller.example.test,IP:192.0.2.10"
```

Replace the example name/IP with your lab controller address. For production or a shared competition network, use your lab CA and distribute its CA certificate to agents. Do not disable certificate verification.

Set a long random token in the environment (same value on controller and agents):
```bash
export C2_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
```
Store it securely. For Ansible, use Ansible Vault; do not commit real tokens or private keys to Git.

## 2. Run controller
```bash
export C2_BIND=0.0.0.0
export C2_PORT=8443
export C2_CERT=certs/server.crt
export C2_KEY=certs/server.key
python3 server.py
```

The controller listens concurrently using a threaded HTTPS server. It accepts agent check-ins and stores recent reports in memory. State resets when the process restarts.

## 3. Run a local agent for testing
```bash
python3 agent.py \
  --server https://c2-controller.example.test:8443 \
  --token "$C2_TOKEN" \
  --ca certs/server.crt \
  --interval 15
```

For a CA-signed certificate, use the CA certificate path with `--ca`. The agent validates TLS certificates and hostname by default.

## 4. Ansible deployment
Edit `ansible/inventory.ini` and `ansible/group_vars/all.yml` to set your lab controller URL and certificate/CA file. Store `c2_token` with Ansible Vault, for example:
```bash
ansible-vault create ansible/group_vars/vault.yml
```
Put `c2_token: "your-long-random-token"` in that file, then run:
```bash
ansible-playbook -i ansible/inventory.ini ansible/deploy.yml --ask-vault-pass
```

Linux targets use a systemd service. Windows targets use a scheduled task running the agent at startup. Python must already be installed on targets; the playbook checks for it rather than installing software from the internet.

## Fixed diagnostics
The agent supports only these server-selected task names:
- `status`: hostname, OS, Python version, and current UTC timestamp
- `disk`: filesystem usage for the root/system drive

The server only queues task names from this allowlist. No task contains a shell command or executable payload. To extend the task set, edit the explicit dispatch table in `agent.py` and the corresponding allowlist in `server.py`.

## Endpoints
- `GET /health`: unauthenticated health check
- `POST /api/checkin`: authenticated agent check-in; returns up to one queued task
- `POST /api/result`: authenticated result submission
- `GET /api/agents`: authenticated view of recent agents and reports
- `POST /api/queue`: authenticated queueing of a fixed diagnostic task

Queue a fixed diagnostic task from the controller host:
```bash
curl --cacert certs/server.crt -H "Authorization: Bearer $C2_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"target":"*","task":"status"}' \
  https://127.0.0.1:8443/api/queue
```
Use an exact `agent_id` instead of `*` to target one agent. The agent ID is shown in the reports returned by `/api/agents`.


The controller is intended for a contained, authorized lab. Restrict inbound access with host firewall rules, use unique credentials where practical, and rotate tokens after the exercise.
