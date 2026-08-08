import json
import os
import re
import time

import requests
from bs4 import BeautifulSoup
from tqdm import tqdm
from urllib.parse import urljoin, urlparse


JSON_FILE = "output/articles.json"

IMAGE_DIR = "output/images"

BASE_URL = "http://blog.livedoor.jp/wp_staffblog/"


HEADERS = {
    "User-Agent":
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Referer":
        BASE_URL
}



# =========================
# 日期
# =========================

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



# =========================
# 下载图片
# =========================

def download_image(
        img_url,
        filename
):


    # livedoor 特殊处理

    if "livedoor.blogimg.jp" in img_url:

        img_url = img_url.replace(
            "https://",
            "http://"
        )


    try:


        # 第一请求
        r = requests.get(
            img_url,
            headers=HEADERS,
            timeout=20,
            allow_redirects=False
        )


        # 如果301，禁止跳HTTPS
        if r.status_code in [
            301,
            302,
            303,
            307,
            308
        ]:


            print(
                "跳转:",
                r.headers.get(
                    "location"
                )
            )


            # 再尝试原地址
            r = requests.get(
                img_url,
                headers=HEADERS,
                timeout=20,
                allow_redirects=False
            )



        if r.status_code != 200:

            print(
                "状态:",
                r.status_code,
                img_url
            )

            return None



        ext = os.path.splitext(
            urlparse(img_url).path
        )[1]


        if ext.lower() not in [
            ".jpg",
            ".jpeg",
            ".png",
            ".gif",
            ".webp"
        ]:

            ext=".jpg"



        save_path=os.path.join(
            IMAGE_DIR,
            filename+ext
        )



        with open(
            save_path,
            "wb"
        ) as f:

            f.write(
                r.content
            )


        return filename+ext



    except Exception as e:


        print(
            "下载异常:",
            img_url,
            e
        )


        return None





# =========================
# 单文章处理
# =========================

def process_article(article):


    url=article["url"]


    try:

        r=requests.get(
            url,
            headers=HEADERS,
            timeout=20
        )

        r.encoding="utf-8"


        soup=BeautifulSoup(
            r.text,
            "html.parser"
        )


    except Exception as e:

        print(
            "文章失败",
            e
        )

        return article



    # title

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


    image_list=[]


    count=1



    for img in main.find_all("img"):


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



        filename=f"{date_key}-{count:02d}"



        saved=download_image(
            img_url,
            filename
        )



        if saved:


            image_list.append(
                {
                    "file":saved,
                    "url":img_url
                }
            )


            count+=1



        time.sleep(
            0.3
        )



    article["images"]=image_list


    return article





# =========================
# MAIN
# =========================

def main():


    os.makedirs(
        IMAGE_DIR,
        exist_ok=True
    )


    with open(
        JSON_FILE,
        encoding="utf-8"
    ) as f:

        articles=json.load(f)



    print(
        "数量:",
        len(articles)
    )



    for article in tqdm(
        articles[:5]
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
        "完成"
    )



if __name__=="__main__":

    main()
