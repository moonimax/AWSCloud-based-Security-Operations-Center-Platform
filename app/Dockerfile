#####
FROM nginx:alpine
COPY src/ /usr/share/nginx/html/
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
# 1단계: 임시 빌더(Alpine)에서 디렉터리 생성 및 권한 설정
FROM alpine:latest AS builder
RUN mkdir -p /var/log/app && chmod 777 /var/log/app

# 2단계: 실제 Juice Shop 이미지로 복사
FROM bkimminich/juice-shop:v17.3.0

# 프로젝트 규격 환경변수 및 포트 설정
ENV PORT=8080
ENV NODE_ENV=unsafe

# 빌더에서 생성한 로그 디렉터리를 복사해옴
COPY --from=builder /var/log/app /var/log/app

EXPOSE 8080
