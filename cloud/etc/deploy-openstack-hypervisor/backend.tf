
terraform {
  backend "http" {
    address = "https://10.177.200.6:7000/1.0/terraformstate/hypervisor-plan"
    update_method = "PUT"
    lock_address = "https://10.177.200.6:7000/1.0/terraformlock/hypervisor-plan"
    lock_method = "PUT"
    unlock_address = "https://10.177.200.6:7000/1.0/terraformunlock/hypervisor-plan"
    unlock_method = "PUT"
    skip_cert_verification = true
  }
}
