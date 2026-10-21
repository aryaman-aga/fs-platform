# Ansible — on-premises / VM provisioning (skeleton, E-ARCH-04)

Roles: `common` (packages, NIC/NPL time sync, SSH hardening, firewall, auditd, automatic security
updates), `docker` (engine + compose plugin, log rotation), `fs_platform` (tagged release checkout,
`ops/.env` from the vault, systemd unit for the compose stack), `backup` (restic env, systemd timers),
`monitoring` (Alertmanager SMTP secret).

```
cp inventory.example.ini inventory.ini
ansible-vault create group_vars/vault.yml     # fs_env (all ops/.env keys), fs_restic_env, vault_alertmanager_smtp_password
ansible-galaxy collection install community.general
ansible-playbook -i inventory.ini site.yml --ask-vault-pass --check
ansible-playbook -i inventory.ini site.yml --ask-vault-pass
```

Deploy **tagged releases only** (`fs_repo_version`), each with a release ticket (E-ITGC-01). Run
`ansible-lint` in CI before changes are merged.
