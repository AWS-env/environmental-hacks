resource "aws_eip_association" "orphan" {
  allocation_id = aws_eip.orphan.id
