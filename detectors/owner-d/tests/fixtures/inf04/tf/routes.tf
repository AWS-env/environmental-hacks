resource "aws_route" "private_default" {
  route_table_id         = "rtb-0123456789abcdef0"
  destination_cidr_block = "0.0.0.0/0"
  nat_gateway_id         = aws_nat_gateway.main.id
}
