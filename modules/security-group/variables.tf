variable "name" {
  description = "Name of the security group."
  type        = string
}

variable "description" {
  description = "Description of the security group."
  type        = string
}

variable "vpc_id" {
  description = "VPC ID for the security group."
  type        = string
}

variable "ingress_rules" {
  description = "List of ingress rules."
  type = list(object({
    from_port                = number
    to_port                  = number
    protocol                 = string
    cidr_blocks              = optional(list(string), [])
    source_security_group_id = optional(string)
    description              = optional(string)
  }))
  default = []
}

variable "egress_rules" {
  description = "List of egress rules."
  type = list(object({
    from_port                = number
    to_port                  = number
    protocol                 = string
    cidr_blocks              = optional(list(string), [])
    source_security_group_id = optional(string)
    description              = optional(string)
  }))
  default = []
}

variable "base_tag" {
  description = "Common tags for all resources."
  type        = map(string)
}

variable "tags" {
  description = "Resource-specific tags."
  type        = map(string)
  default     = {}
}
