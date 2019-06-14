```shell
docker build -t anime-feed:1 .

docker run --rm -v ~/.aws/credentials:/root/.aws/credentials -e environment=development anime-feed:1

docker rmi anime-feed:1
```
