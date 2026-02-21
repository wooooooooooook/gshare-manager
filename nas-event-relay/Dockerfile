FROM alpine:3.20
RUN apk add --no-cache bash inotify-tools curl
WORKDIR /app
COPY watch-and-notify.sh /app/watch-and-notify.sh
RUN chmod +x /app/watch-and-notify.sh
CMD ["/app/watch-and-notify.sh"]
