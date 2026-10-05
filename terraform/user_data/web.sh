#!/bin/bash
# =============================================================================
# 웹서버 부팅 스크립트 (Terraform templatefile로 렌더링됨)
#  - 주의: 이 파일에서 달러+중괄호 표기는 Terraform 변수입니다.
#          셸 변수는 중괄호 없이 $VAR 형태로만 사용하세요.
# 구성: Docker + Nginx(리버스 프록시, JSON 로그, realip) + 앱 컨테이너
#       + (선택) Node Exporter(:9100) + cAdvisor(:8080) + CloudWatch Agent(설정·실행)
# =============================================================================
set -eux
exec > >(tee -a /var/log/user-data.log) 2>&1

REGION="${region}"
APP_IMAGE="${app_image}"

# -----------------------------------------------------------------------------
# 1. 패키지 설치
# -----------------------------------------------------------------------------
dnf update -y
dnf install -y docker nginx amazon-cloudwatch-agent
systemctl enable --now docker

# -----------------------------------------------------------------------------
# 2. 앱 컨테이너 (호스트의 127.0.0.1:8000에만 노출 → 외부 직접 접근 불가)
#    8080은 cAdvisor가 사용하므로 앱은 8000 사용
# -----------------------------------------------------------------------------
if echo "$APP_IMAGE" | grep -q '\.dkr\.ecr\.'; then
  REGISTRY=$(echo "$APP_IMAGE" | cut -d/ -f1)
  aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$REGISTRY"
fi

# 앱 보안 이벤트 로그 디렉터리 (로그인 실패, 인가 거부 등 — 앱이 여기에 기록)
mkdir -p /var/log/app
chmod 755 /var/log/app

docker run -d --name app --restart unless-stopped \
  -p 127.0.0.1:8000:3000 \
  -v /var/log/app:/var/log/app \
  "$APP_IMAGE"

# -----------------------------------------------------------------------------
# 3. Nginx: 리버스 프록시 + JSON 액세스 로그 (Logstash 파싱용)
# -----------------------------------------------------------------------------
cat > /etc/nginx/nginx.conf << 'NGINX'
user nginx;
worker_processes auto;
error_log /var/log/nginx/error.log warn;
pid /run/nginx.pid;

events { worker_connections 1024; }

http {
    include      /etc/nginx/mime.types;
    default_type application/octet-stream;
    server_tokens off;

    # 파일 업로드 검증 시나리오 테스트를 위해 기본값(1m)보다 상향
    client_max_body_size ${upload_max_size};

    # ALB 뒤에서 실제 클라이언트 IP 복원
    #  - VPC 내부(ALB)에서 온 요청만 신뢰하고, 그 밖의 마지막 IP를 클라이언트로 인식
    #  - 공격자가 X-Forwarded-For를 위조해도 IP별 탐지가 왜곡되지 않음
    set_real_ip_from ${vpc_cidr};
    real_ip_header   X-Forwarded-For;
    real_ip_recursive on;

    log_format json_log escape=json '{'
        '"time":"$time_iso8601",'
        '"ip":"$remote_addr",'
        '"xff_raw":"$http_x_forwarded_for",'
        '"method":"$request_method",'
        '"path":"$request_uri",'
        '"request_uri":"$request_uri",'
        '"status":$status,'
        '"bytes_sent":$body_bytes_sent,'
        '"request_time":$request_time,'
        '"upstream_time":"$upstream_response_time",'
        '"referer":"$http_referer",'
        '"user_agent":"$http_user_agent",'
        '"host":"$hostname"'
    '}';

    access_log /var/log/nginx/access.log json_log;

    server {
        listen 80 default_server;
        server_name _;

        # ALB 헬스체크 전용 (로그 제외)
        location = /healthz {
            access_log off;
            return 200 "ok\n";
        }

        location / {
            proxy_pass         http://127.0.0.1:8000;
            proxy_set_header   Host              $host;
            proxy_set_header   X-Real-IP         $remote_addr;
            proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
            proxy_set_header   X-Forwarded-Proto $scheme;
        }
    }
}
NGINX

nginx -t
systemctl enable --now nginx

# -----------------------------------------------------------------------------
# 5. CloudWatch Agent — SSM Parameter Store 설정을 부팅 시 자동 적용
#    수집 대상 로그: /var/log/nginx/access.log (JSON)
# -----------------------------------------------------------------------------
/opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl \
  -a fetch-config -m ec2 -s -c ssm:${cwagent_ssm_param}


# -----------------------------------------------------------------------------
# 4. 메트릭 Exporter — 공급망 공격 대비 latest 대신 버전 고정
#    메트릭을 CloudWatch로만 수집한다면 enable_exporters=false로 비활성화
# -----------------------------------------------------------------------------
%{ if enable_exporters ~}
docker run -d --name node-exporter --restart unless-stopped \
  --net host --pid host \
  -v /:/host:ro,rslave \
  quay.io/prometheus/node-exporter:v1.8.2 \
  --path.rootfs=/host

docker run -d --name cadvisor --restart unless-stopped \
  -p 8080:8080 \
  -v /:/rootfs:ro \
  -v /var/run:/var/run:ro \
  -v /sys:/sys:ro \
  -v /var/lib/docker/:/var/lib/docker:ro \
  -v /dev/disk/:/dev/disk:ro \
  --privileged --device /dev/kmsg \
  gcr.io/cadvisor/cadvisor:v0.49.1
%{ endif ~}

echo "user-data finished"
