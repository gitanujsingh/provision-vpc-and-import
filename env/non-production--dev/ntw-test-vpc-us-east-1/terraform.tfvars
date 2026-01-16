base_tag = {
  Region       = "us-east-1"
  application  = "ntw"
  environment  = "Non-production::Dev"
  "created by" = "Cloud Network Team"
}
environment = "Non-production::Dev"
region      = "us-east-1"

vpc_cidr = "10.67.0.0/24"

additional_cidrs = [
  "100.67.0.0/25",
  "100.67.1.0/24",
]

public_subnet_cidrs = [
  "100.67.0.0/28",
  "100.67.0.16/28",
  "100.67.0.32/28",
]

private_subnet_cidrs = [
  "10.67.0.0/28",
  "10.67.0.16/28",
  "10.67.0.32/28",
]

nonroutable_subnet_cidrs = [
  "100.67.1.0/26",
  "100.67.1.64/26",
  "100.67.1.128/26",
]

azs = [
  "us-east-1a",
  "us-east-1b",
  "us-east-1c",
]

vpc_name = "ntw-test-vpc-us-east-1"

# Provisioning toggles
enable_additional_cidrs          = true
enable_public_subnets            = true
enable_private_subnets           = true
enable_nonroutable_subnets       = true
enable_gateways                  = true
enable_internet_gateway          = true
enable_public_nat_gateways       = true
enable_private_nat_gateways      = true
enable_public_route_table        = true
enable_private_route_tables      = true
enable_nonroutable_route_tables  = true
enable_nacls                     = true
enable_public_nacl               = true
enable_private_nonroutable_nacl  = true
enable_s3_gateway_endpoint       = true
enable_interface_endpoints       = true
enable_vpc_endpoints_sg          = false  # Will use endpoint-sg from extra_security_groups
vpc_endpoints_security_group_ids = ["DYNAMIC"]  # Placeholder - will be resolved in main.tf

# Interface VPC Endpoints - matching imported VPC
interface_vpc_endpoints = {
  "ec2" = {
    private_dns_enabled = true
    tags                = {}
  }
  "ec2messages" = {
    private_dns_enabled = true
    tags                = {}
  }
  "ssm" = {
    private_dns_enabled = true
    tags                = {}
  }
  "ssmmessages" = {
    private_dns_enabled = true
    tags                = {}
  }
}

# NACL rules
nacl_rules = {
  public_ingress = []
  public_egress  = []
  private_ingress = []
  private_egress = []
}

# Security groups - matching imported VPC structure
extra_security_groups = {
  "app-sg" = {
    name        = "app-sg"
    description = "Application security group"
    ingress_rules = [
      {
        from_port   = 443
        to_port     = 443
        protocol    = "tcp"
        cidr_blocks = ["10.67.0.0/24"]
        description = "Allow HTTPS from VPC"
      }
    ]
    egress_rules = [
      {
        from_port   = 0
        to_port     = 0
        protocol    = "-1"
        cidr_blocks = ["0.0.0.0/0"]
        description = "Allow all outbound"
      }
    ]
    tags = {
      Name = "app-sg"
    }
  }
  "no-ingress-sg" = {
    name        = "no-ingress-sg"
    description = "Security group with no ingress rule"
    ingress_rules = []
    egress_rules = [
      {
        from_port   = 0
        to_port     = 0
        protocol    = "-1"
        cidr_blocks = ["0.0.0.0/0"]
        description = "Allow all outbound"
      }
    ]
    tags = {
      Name = "no-ingress-sg"
    }
  }
  "endpoint-sg" = {
    name        = "endpoint-sg"
    description = "Security group for VPC endpoints"
    ingress_rules = [
      {
        from_port   = 443
        to_port     = 443
        protocol    = "tcp"
        cidr_blocks = ["10.0.0.0/8"]
        description = "Allow HTTPS from 10.0.0.0/8"
      }
    ]
    egress_rules = [
      {
        from_port   = 0
        to_port     = 0
        protocol    = "-1"
        cidr_blocks = ["0.0.0.0/0"]
        description = "Allow all outbound"
      }
    ]
    tags = {
      Name = "endpoint-sg"
    }
  }
}

# Extra routes - using dynamic references to NAT gateways
public_extra_routes = []
private_extra_routes = []
# For nonroutable extra routes, we need to reference the private NAT gateways dynamically
# Each nonroutable subnet has a corresponding private NAT gateway
# The route will be added to ALL nonroutable route tables pointing to the NAT in each respective subnet
nonroutable_extra_routes = [
  {
    destination_cidr_block = "10.0.0.0/8"
    target_type            = "nat_gateway_id"
    target_id              = "local"  # Special marker meaning "use the NAT gateway in the same subnet as this route table"
  },
]


# DHCP Options (must be last)
domain_name          = "ec2.internal"
domain_name_servers  = ["AmazonProvidedDNS"]
ntp_servers          = ["0.0.0.0"]
netbios_name_servers = ["192.168.1.1"]
netbios_node_type    = 2