# Synthetic fixture (INF07-05): Terraform is not supported by INF-07 v1.
resource "aws_instance" "web" {
  instance_type = "m4.large"
}
