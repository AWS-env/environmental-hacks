output "alb_dns_name" {
  value = aws_lb.public.dns_name
}

output "lookup_ip" {
  value = data.aws_eip.lookup.public_ip
}
