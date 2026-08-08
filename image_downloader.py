import json
import os
import re
import time

import requests
from bs4 import BeautifulSoup
from tqdm import tqdm
from urllib.parse import urljoin, urlparse


# ==========================
# GitHub环境配置
# ==========================

JSON_FILE = "articles.json"

IMAGE_DIR = "images"

BASE_URL = "http://blog.livedoor.jp/wp_staffblog/"


HEADERS = {
    "User-Agent":
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Referer":
        BASE_URL
}



# ==========================
# 日期转换
# ==========================

def format_date(date):

    nums = re.findall(
        r"\d+",
        date
    )

    if len(nums) >= 3:

        return (
            nums[0]
            +
            nums[1].zfill(2)
            +
            nums[2].zfill(2)
        )

    return "unknown"



# ==========================
# 下载图片
# ==========================

def download_image(
        url,
        filename
):

    try:


        r = requests.get(
            url,
            headers=HEADERS,
            timeout=30
        )


        if r.status_code != 200:

            print(
                "图片状态错误:",
                r.status_code,
                url
            )

            return None



        ext = os.path.splitext(
            urlparse(url).path
        )[1]


        if ext.lower() not in [
            ".jpg",
            ".jpeg",
            ".png",
            ".gif",
            ".webp"
        ]:

            ext=".jpg"



        path=os.path.join(
            IMAGE_DIR,
            filename+ext
        )



        with open(
            path,
            "wb"
        ) as f:

            f.write(
                r.content
            )


        return filename+ext



    except Exception as e:


        print(
            "图片下载失败:",
            url,
            e
        )


        return None





# ==========================
# 单文章处理
# ==========================

def process_article(article):


    url=article.get(
        "url"
    )


    if not url:

        return article



    try:

        r=requests.get(
            url,
            headers=HEADERS,
            timeout=30
        )

        r.encoding="utf-8"


        soup=BeautifulSoup(
            r.text,
            "html.parser"
        )


    except Exception as e:


        print(
            "文章访问失败:",
            url,
            e
        )

        return article



    # 修复标题

    title=soup.select_one(
        "h3.entry-title a"
    )


    if title:

        article["title"]=title.get_text(
            strip=True
        )



    main=soup.select_one(
        "div.main"
    )


    if not main:

        return article



    date_key=format_date(
        article.get(
            "date",
            ""
        )
    )



    images=[]


    index=1



    for img in main.find_all(
        "img"
    ):


        src = (
            img.get("src")
            or
            img.get("data-src")
            or
            img.get("data-original")
        )


        if not src:

            continue



        img_url=urljoin(
            BASE_URL,
            src
        )



        filename=f"{date_key}-{index:02d}"



        saved=download_image(
            img_url,
            filename
        )



        if saved:


            images.append(
                {
                    "file": saved,
                    "url": img_url
                }
            )


            index += 1



        time.sleep(
            0.5
        )



    article["images"]=images


    return article





# ==========================
# 主程序
# ==========================

def main():


    os.makedirs(
        IMAGE_DIR,
        exist_ok=True
    )


    if not os.path.exists(
        JSON_FILE
    ):

        raise FileNotFoundError(
            f"找不到 {JSON_FILE}，请确认上传到仓库根目录"
        )



    with open(
        JSON_FILE,
        "r",
        encoding="utf-8"
    ) as f:

        articles=json.load(f)



    print(
        "文章数量:",
        len(articles)
    )



    for article in tqdm(
        articles
    ):


        process_article(
            article
        )


        time.sleep(
            1
        )



    with open(
        JSON_FILE,
        "w",
        encoding="utf-8"
    ) as f:


        json.dump(
            articles,
            f,
            ensure_ascii=False,
            indent=2
        )



    print(
        "全部完成"
    )



if __name__=="__main__":

    main()
