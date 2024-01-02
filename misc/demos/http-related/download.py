import requests
from threading import Thread


class picDownloadThread(Thread):
    def __init__(self, url):
        super().__init__()
        self.url = url

    def run(self):
        picName = self.url[self.url.rfind('/') + 1:]
        fireR = open(picName, 'wb')
        picTure = requests.get(self.url)
        fireR.write(picTure.content)
        fireR.close()


def main():
    # 调用天行数据美女接口，格式：http://api.tianapi.com/meinv/index?key=APIKEY&num=10
    # 将APIKEY替换成自己的key就可以了，需要注册才能生成自己的key
    reqData = requests.get("http://api.tianapi.com/meinv/index?key=a9953cce0eda88de24f4c4ebe3cadf70&num=10")
    # 将返回的数据字符串转换成json格式
    urlDic = reqData.json()
    # print(urlDic.content)
    for oneDic in urlDic['newslist']:
        url = oneDic['picUrl']
        # 启用多线程下载图片
        picDownload = picDownloadThread(url)
        picDownload.start()
        picDownload.join()


if __name__ == "__main__":
    main()
