
variable "machine_ids" {
  description = "List of machine ids to include"
  type        = list(string)
  default     = []
}

variable "snap_channel" {
  description = "Snap channel"
  type        = string
}

variable "charm_channel" {
  description = "Charm channel"
  type        = string
}

variable "placement" {
  description = "Machine numbers to target"
  type        = string
}

variable "openstack_model" {
  description = "Machine numbers to target"
  type        = string
}

variable "hypervisor_model" {
  description = "Machine numbers to target"
  type        = string
}

