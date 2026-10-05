output "public_ip" { value = aws_instance.monitor.public_ip }
output "instance_id" { value = aws_instance.monitor.id }
output "data_volume_id" { value = aws_ebs_volume.data.id }
output "ssh_tunnel" {
  value = "ssh -i YOUR_KEY.pem -L 8080:127.0.0.1:8080 ubuntu@${aws_instance.monitor.public_ip}"
}
output "ui_url" { value = "http://localhost:8080" }
output "login_command" { value = "EC2에서 sudo cat /srv/monitoring-data/secrets/credentials.txt" }
