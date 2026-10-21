# FS-Platform on Azure Central India (04 §8 option 2; DR target for option 1, E-BCP-03).
# Two Linux VMs (app, data), one Windows render VM, private networking only, backup storage with
# versioning + immutability, Key Vault for KMS keys, Log Analytics with >= 180-day retention.
# Ingress is through Cloudflare Tunnel (outbound only) — no public IPs on any VM.

data "azurerm_client_config" "current" {}

resource "azurerm_resource_group" "fs" {
  name     = "${var.prefix}-rg"
  location = var.location
  tags     = var.tags
}

# ------------------------------------------------------------------ network
resource "azurerm_virtual_network" "fs" {
  name                = "${var.prefix}-vnet"
  location            = azurerm_resource_group.fs.location
  resource_group_name = azurerm_resource_group.fs.name
  address_space       = ["10.40.0.0/16"]
  tags                = var.tags
}

resource "azurerm_subnet" "app" {
  name                 = "app"
  resource_group_name  = azurerm_resource_group.fs.name
  virtual_network_name = azurerm_virtual_network.fs.name
  address_prefixes     = ["10.40.1.0/24"]
}

resource "azurerm_subnet" "data" {
  name                 = "data"
  resource_group_name  = azurerm_resource_group.fs.name
  virtual_network_name = azurerm_virtual_network.fs.name
  address_prefixes     = ["10.40.2.0/24"]
}

resource "azurerm_subnet" "render" {
  name                 = "render"
  resource_group_name  = azurerm_resource_group.fs.name
  virtual_network_name = azurerm_virtual_network.fs.name
  address_prefixes     = ["10.40.3.0/24"]
}

resource "azurerm_network_security_group" "app" {
  name                = "${var.prefix}-app-nsg"
  location            = azurerm_resource_group.fs.location
  resource_group_name = azurerm_resource_group.fs.name
  tags                = var.tags

  security_rule {
    name                       = "ssh-from-admin-networks"
    priority                   = 100
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "22"
    source_address_prefixes    = var.admin_cidrs
    destination_address_prefix = "*"
  }

  security_rule {
    name                       = "https-from-render-subnet"
    priority                   = 110
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "443"
    source_address_prefix      = "10.40.3.0/24"
    destination_address_prefix = "*"
  }

  security_rule {
    name                       = "deny-internet-inbound"
    priority                   = 4000
    direction                  = "Inbound"
    access                     = "Deny"
    protocol                   = "*"
    source_port_range          = "*"
    destination_port_range     = "*"
    source_address_prefix      = "Internet"
    destination_address_prefix = "*"
  }
}

resource "azurerm_network_security_group" "data" {
  name                = "${var.prefix}-data-nsg"
  location            = azurerm_resource_group.fs.location
  resource_group_name = azurerm_resource_group.fs.name
  tags                = var.tags

  security_rule {
    name                       = "postgres-minio-from-app"
    priority                   = 100
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_ranges    = ["5432", "9000"]
    source_address_prefix      = "10.40.1.0/24"
    destination_address_prefix = "*"
  }

  security_rule {
    name                       = "ssh-from-admin-networks"
    priority                   = 110
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "22"
    source_address_prefixes    = var.admin_cidrs
    destination_address_prefix = "*"
  }

  security_rule {
    name                       = "ntp-outbound" # CERT-In: clocks synchronised to NIC / NPL time servers
    priority                   = 3900
    direction                  = "Outbound"
    access                     = "Allow"
    protocol                   = "Udp"
    source_port_range          = "*"
    destination_port_range     = "123"
    source_address_prefix      = "*"
    destination_address_prefix = "Internet"
  }

  security_rule {
    name                       = "deny-internet-outbound" # OS updates come through the firm's package mirror / proxy
    priority                   = 4000
    direction                  = "Outbound"
    access                     = "Deny"
    protocol                   = "*"
    source_port_range          = "*"
    destination_port_range     = "*"
    source_address_prefix      = "*"
    destination_address_prefix = "Internet"
  }
}

resource "azurerm_network_security_group" "render" {
  name                = "${var.prefix}-render-nsg"
  location            = azurerm_resource_group.fs.location
  resource_group_name = azurerm_resource_group.fs.name
  tags                = var.tags

  security_rule {
    name                       = "rdp-from-admin-networks"
    priority                   = 100
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "3389"
    source_address_prefixes    = var.admin_cidrs
    destination_address_prefix = "*"
  }
}

resource "azurerm_subnet_network_security_group_association" "app" {
  subnet_id                 = azurerm_subnet.app.id
  network_security_group_id = azurerm_network_security_group.app.id
}

resource "azurerm_subnet_network_security_group_association" "data" {
  subnet_id                 = azurerm_subnet.data.id
  network_security_group_id = azurerm_network_security_group.data.id
}

resource "azurerm_subnet_network_security_group_association" "render" {
  subnet_id                 = azurerm_subnet.render.id
  network_security_group_id = azurerm_network_security_group.render.id
}

# ------------------------------------------------------------------ compute
resource "azurerm_network_interface" "app" {
  name                = "${var.prefix}-app-nic"
  location            = azurerm_resource_group.fs.location
  resource_group_name = azurerm_resource_group.fs.name
  tags                = var.tags

  ip_configuration {
    name                          = "internal"
    subnet_id                     = azurerm_subnet.app.id
    private_ip_address_allocation = "Dynamic"
  }
}

resource "azurerm_network_interface" "data" {
  name                = "${var.prefix}-data-nic"
  location            = azurerm_resource_group.fs.location
  resource_group_name = azurerm_resource_group.fs.name
  tags                = var.tags

  ip_configuration {
    name                          = "internal"
    subnet_id                     = azurerm_subnet.data.id
    private_ip_address_allocation = "Dynamic"
  }
}

resource "azurerm_network_interface" "render" {
  name                = "${var.prefix}-render-nic"
  location            = azurerm_resource_group.fs.location
  resource_group_name = azurerm_resource_group.fs.name
  tags                = var.tags

  ip_configuration {
    name                          = "internal"
    subnet_id                     = azurerm_subnet.render.id
    private_ip_address_allocation = "Dynamic"
  }
}

resource "azurerm_linux_virtual_machine" "app" {
  name                            = "${var.prefix}-app-1"
  location                        = azurerm_resource_group.fs.location
  resource_group_name             = azurerm_resource_group.fs.name
  size                            = var.app_vm_size
  admin_username                  = "fsadmin"
  disable_password_authentication = true
  network_interface_ids           = [azurerm_network_interface.app.id]
  encryption_at_host_enabled      = true
  tags                            = var.tags

  admin_ssh_key {
    username   = "fsadmin"
    public_key = var.admin_ssh_public_key
  }

  os_disk {
    caching              = "ReadWrite"
    storage_account_type = "Premium_LRS"
    disk_size_gb         = 128
  }

  source_image_reference {
    publisher = "Canonical"
    offer     = "ubuntu-24_04-lts"
    sku       = "server"
    version   = "latest"
  }
}

resource "azurerm_linux_virtual_machine" "data" {
  name                            = "${var.prefix}-data-1"
  location                        = azurerm_resource_group.fs.location
  resource_group_name             = azurerm_resource_group.fs.name
  size                            = var.data_vm_size
  admin_username                  = "fsadmin"
  disable_password_authentication = true
  network_interface_ids           = [azurerm_network_interface.data.id]
  encryption_at_host_enabled      = true
  tags                            = var.tags

  admin_ssh_key {
    username   = "fsadmin"
    public_key = var.admin_ssh_public_key
  }

  os_disk {
    caching              = "ReadWrite"
    storage_account_type = "Premium_LRS"
    disk_size_gb         = 128
  }

  source_image_reference {
    publisher = "Canonical"
    offer     = "ubuntu-24_04-lts"
    sku       = "server"
    version   = "latest"
  }
}

resource "azurerm_managed_disk" "data" {
  name                 = "${var.prefix}-data-disk"
  location             = azurerm_resource_group.fs.location
  resource_group_name  = azurerm_resource_group.fs.name
  storage_account_type = "Premium_LRS"
  create_option        = "Empty"
  disk_size_gb         = 1024
  tags                 = var.tags
}

resource "azurerm_virtual_machine_data_disk_attachment" "data" {
  managed_disk_id    = azurerm_managed_disk.data.id
  virtual_machine_id = azurerm_linux_virtual_machine.data.id
  lun                = 0
  caching            = "ReadOnly"
}

resource "azurerm_windows_virtual_machine" "render" {
  name                       = "${var.prefix}-render-1"
  location                   = azurerm_resource_group.fs.location
  resource_group_name        = azurerm_resource_group.fs.name
  size                       = var.render_vm_size
  admin_username             = "fsrenderadmin"
  admin_password             = var.windows_admin_password
  network_interface_ids      = [azurerm_network_interface.render.id]
  encryption_at_host_enabled = true
  tags                       = var.tags

  os_disk {
    caching              = "ReadWrite"
    storage_account_type = "Premium_LRS"
  }

  source_image_reference {
    publisher = "MicrosoftWindowsServer"
    offer     = "WindowsServer"
    sku       = "2022-datacenter-azure-edition"
    version   = "latest"
  }
}

# ------------------------------------------------------------------ backups, keys, logs
resource "azurerm_storage_account" "backup" {
  name                            = "${var.prefix}backup${substr(md5(azurerm_resource_group.fs.id), 0, 6)}"
  location                        = azurerm_resource_group.fs.location
  resource_group_name             = azurerm_resource_group.fs.name
  account_tier                    = "Standard"
  account_replication_type        = "ZRS"
  min_tls_version                 = "TLS1_2"
  allow_nested_items_to_be_public = false
  public_network_access_enabled   = true # restic reaches it over HTTPS; restrict with network_rules below
  tags                            = var.tags

  blob_properties {
    versioning_enabled = true
    delete_retention_policy {
      days = 35
    }
  }

  immutability_policy {
    allow_protected_append_writes = true
    state                         = "Unlocked" # lock it (state = "Locked") after the first verified backup
    period_since_creation_in_days = var.backup_immutability_days
  }

  network_rules {
    default_action = "Deny"
    ip_rules       = [for cidr in var.admin_cidrs : replace(cidr, "/32", "")]
    bypass         = ["AzureServices"]
  }
}

resource "azurerm_key_vault" "fs" {
  name                       = "${var.prefix}-kv-${substr(md5(azurerm_resource_group.fs.id), 0, 6)}"
  location                   = azurerm_resource_group.fs.location
  resource_group_name        = azurerm_resource_group.fs.name
  tenant_id                  = data.azurerm_client_config.current.tenant_id
  sku_name                   = "premium" # HSM-backed keys for the KEK (E-SEC-07)
  purge_protection_enabled   = true
  soft_delete_retention_days = 90
  tags                       = var.tags
}

resource "azurerm_log_analytics_workspace" "fs" {
  name                = "${var.prefix}-logs"
  location            = azurerm_resource_group.fs.location
  resource_group_name = azurerm_resource_group.fs.name
  sku                 = "PerGB2018"
  retention_in_days   = var.log_retention_days
  tags                = var.tags
}
