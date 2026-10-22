# ========================================
# BidMind AI Lite - Nginx Dockerfile
# 前端静态文件托管 + API反向代理
# ========================================

# 可配置镜像仓库前缀（国内加速）
# 默认空串 = Docker Hub 官方; 国内可传 docker.m.daocloud.io/
ARG REGISTRY=""
FROM ${REGISTRY}nginx:alpine

# 删除默认配置
RUN rm /etc/nginx/conf.d/default.conf

# 复制自定义配置
COPY nginx.conf /etc/nginx/nginx.conf

EXPOSE 80 443

CMD ["nginx", "-g", "daemon off;"]
