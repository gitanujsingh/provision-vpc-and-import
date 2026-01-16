# DHCP Options
domain_name          = "ec2.internal"
domain_name_servers  = ["AmazonProvidedDNS"]
ntp_servers          = ["0.0.0.0"]
netbios_name_servers = ["192.168.1.1"]
netbios_node_type    = 2
base_tag = {
  Region       = "us-east-1"
  application  = "ntw"
  environment  = "dev"
  "created by" = "Cloud Network Team"
}
region = "us-east-1"

vpc_cidr = "10.63.0.0/24"

############################
# Provisioning toggles
# Flip these to true/false to create/remove resources.
############################

enable_additional_cidrs     = true
enable_public_subnets       = true
enable_private_subnets      = true
enable_nonroutable_subnets  = true

enable_gateways             = true
enable_internet_gateway     = true
enable_public_nat_gateways  = true
enable_private_nat_gateways = true

enable_public_route_table      = true
enable_private_route_tables    = true
enable_nonroutable_route_tables = true

enable_nacls                    = true
enable_public_nacl              = true
enable_private_nonroutable_nacl = true

enable_s3_gateway_endpoint  = true
enable_interface_endpoints  = true
enable_vpc_endpoints_sg     = true

vpc_endpoints_security_group_ids = []

additional_cidrs = [
  "100.63.0.0/20",
  "100.62.0.0/24",
]

public_subnet_cidrs = [
  "100.63.0.0/26",
  "100.63.0.64/26",
  "100.63.0.128/26",
]

private_subnet_cidrs = [
  "10.63.0.0/28",
  "10.63.0.16/28",
  "10.63.0.32/28",
]

nonroutable_subnet_cidrs = [
  "100.62.0.0/28",
  "100.62.0.16/28",
  "100.62.0.32/28",
]

nacl_rules = {
  public_ingress = []
  public_egress  = []

  private_ingress = [
    {
      rule_number = 100
      protocol    = "-1"
      rule_action = "allow"
      cidr_block  = "10.0.0.0/8"
      from_port   = 0
      to_port     = 0
    }
  ]
  private_egress = []
}

security_groups = {}

public_extra_routes     = []
private_extra_routes    = []
nonroutable_extra_routes = []

azs = [
  "us-east-1a",
  "us-east-1b",
  "us-east-1c"
]

# Add vpc_name for all modules
vpc_name = "ntw-dev-demo-2-vpc"