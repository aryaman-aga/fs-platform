output "app_private_ip" {
  description = "Private IP of the application node (Caddy, API, workers)."
  value       = azurerm_network_interface.app.private_ip_address
}

output "data_private_ip" {
  description = "Private IP of the data node (PostgreSQL, MinIO)."
  value       = azurerm_network_interface.data.private_ip_address
}

output "render_private_ip" {
  description = "Private IP of the Windows render worker."
  value       = azurerm_network_interface.render.private_ip_address
}

output "backup_storage_account" {
  description = "Storage account for the off-site restic repository (create the container with immutability)."
  value       = azurerm_storage_account.backup.name
}

output "key_vault_uri" {
  description = "Key Vault holding the KEK and backup key material."
  value       = azurerm_key_vault.fs.vault_uri
}
