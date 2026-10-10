# Synthetic fixture (INF04-04): a Terraform module split across main.tf, routes.tf and outputs.tf.
resource "aws_eip" "nat" {
  domain = "vpc"
}

resource "aws_nat_gateway" "main" {
  allocation_id = aws_eip.nat.id
  subnet_id     = "subnet-0123456789abcdef0"
}

resource "aws_eip" "orphan" {
  domain = "vpc"
}

resource "aws_eip" "web" {
  instance = "i-0abc12345678def00"
  domain   = "vpc"
}

resource "aws_lb" "public" {
  name               = "public"
  load_balancer_type = "application"
  subnets            = ["subnet-0123456789abcdef0", "subnet-0fedcba9876543210"]
}

resource "aws_ebs_volume" "disabled" {
  count             = 0
  availability_zone = "ap-south-1a"
  size              = 10
}

resource "aws_ebs_volume" "scratch" {
  for_each          = toset(["a", "b"])
  availability_zone = "ap-south-1a"
  size              = 10
}

resource "aws_nat_gateway" "spare" {
  allocation_id = "eipalloc-0123456789abcdef0"
  subnet_id     = "subnet-0123456789abcdef0"
}

# Kept for the failover runbook.  # noqa: INF-04
resource "aws_eip" "standby" {
  domain = "vpc"
}

data "aws_eip" "lookup" {
  public_ip = "203.0.113.10"
}
