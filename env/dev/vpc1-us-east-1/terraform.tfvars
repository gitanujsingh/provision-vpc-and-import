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
environment = "dev"
region      = "us-east-1"

vpc_cidr = "10.65.0.0/24"

additional_cidrs = [
  "100.65.0.0/20",
  "100.64.0.0/24",
]

public_subnet_cidrs = [
  "100.65.0.0/26",
  "100.65.0.64/26",
  "100.65.0.128/26",
]

private_subnet_cidrs = [
  "10.65.0.0/28",
  "10.65.0.16/28",
  "10.65.0.32/28",
]

nonroutable_subnet_cidrs = [
  "100.64.0.0/28",
  "100.64.0.16/28",
  "100.64.0.32/28",
]


############################
# Provisioning toggles
# Flip these to true/false to create/remove resources.
############################

enable_additional_cidrs     = true
enable_public_subnets       =  true
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

# If you want to use an existing SG for interface endpoints, set:
# enable_vpc_endpoints_sg = false
# vpc_endpoints_security_group_ids = ["sg-xxxxxxxx"]
vpc_endpoints_security_group_ids = []

############################
#   NACL rule management (add/remove rules here)
############################

nacl_rules = {
  public_ingress = []
  public_egress  = []

  # Preserves previous default behavior (allow all from 10.0.0.0/8 into private/nonroutable)
  private_ingress = [
    {
      rule_number = 100
      protocol    = "-1"
      rule_action = "allow"
      cidr_block  = "10.0.0.0/8"
      from_port   = 0
      to_port     = 0
    },
    {
      rule_number = 110
      protocol="6"
      rule_action = "allow"
      cidr_block  = "192.168.0.0/24"
      from_port   = 0
      to_port     = 0

    }
  ]
  private_egress = []
}

############################
#   extra security groups (create/remove by editing this map)
############################

security_groups = {  demo_sg = {
    name        = "demo-sg"
    description = "demo security group"
    
    tags = {
      Name        = "demo-sg"
      Environment = "ntw-dev-demo-vpc"
    }


    ingress_rules = [
      {
        from_port   = 443
        to_port     = 443
        protocol    = "tcp"
        cidr_blocks = ["10.65.0.0/24"]
        description = "Allow HTTPS from within VPC"
      },
      {
        from_port   = 2048
        to_port     = 2048
        protocol    = "tcp"
        cidr_blocks = ["10.65.0.0/24"]
        description = "Allow TCP 2048 from within VPC"
      },
      {
        from_port   = 80
        to_port     = 80
        protocol    = "tcp"
        cidr_blocks = ["10.65.0.0/24"]
        description = "Allow HTTP from within VPC"
      }
    ]

    # optional, but recommended so the SG is usable
    egress_rules = [
      {
        from_port   = 0
        to_port     = 0
        protocol    = "-1"
        cidr_blocks = ["0.0.0.0/0"]
        description = "Allow all outbound"
      }
    ]
  }
}

############################
#   extra routes (create/remove by editing these lists)
############################

#public_extra_routes     = []
private_extra_routes    = []
nonroutable_extra_routes = []

public_extra_routes = [
  {
    destination_cidr_block = "203.0.113.0/24"
    target_type            = "gateway_id"
    target_id              = "igw"
  }
]

azs = [
  "us-east-1a",
  "us-east-1b",
  "us-east-1c"
]

# Add vpc_name for all modules
vpc_name = "ntw-dev-demo-vpc"

# VPC1-only: add an extra private route
enable_private_onprem_route     = false
private_onprem_destination_cidr = "10.0.0.0/8"