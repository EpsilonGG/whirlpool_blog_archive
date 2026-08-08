import requests
from bs4 import BeautifulSoup
import json
import os
import time
import re
from tqdm import tqdm
from urllib.parse import urljoin, urlparse


BASE_URL = "http://blog.livedoor.jp/wp_staffblog/"

OUTPUT = "output/articles.json"

IMAGE_DIR = "output/images"


headers = {
    "User-Agent":
    "Mozilla/5.0"
}



# ======================
# 日期转换
# ======================

def format_date(date):

    """
    输入:
    2010年03月03日

    输出:
    20100303
    """

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




# ======================
# 获取分页
# ======================

def get_page(page):

    url = f"{BASE_URL}?p={page}"

    r=requests.get(
        url,
        headers=headers,
        timeout=15
    )

    r.encoding="utf-8"

    return r.text




# ======================
# 获取文章URL
# ======================

def get_article_links(html):

    soup=BeautifulSoup(
        html,
        "lxml"
    )

    links=set()


    for a in soup.select(
        "h3.entry-title a"
    ):

        href=a.get("href")

        if href:

            links.add(href)


    return links




# ======================
# 下载图片
# ======================

def download_images(
        content,
        date_key
):

    os.makedirs(
        IMAGE_DIR,
        exist_ok=True
    )


    images=[]


    img_tags=content.select(
        "img"
    )


    count=1


    for img in img_tags:


        src=img.get(
            "src"
        )


        if not src:
            continue



        img_url=urljoin(
            BASE_URL,
            src
        )



        try:


            r=requests.get(
                img_url,
                headers=headers,
                timeout=20
            )


            if r.status_code != 200:

                continue



            # 获取后缀

            ext=os.path.splitext(
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



            if count == 1:

                filename=f"{date_key}{ext}"

            else:

                filename=f"{date_key}_{count}{ext}"



            path=os.path.join(
                IMAGE_DIR,
                filename
            )



            with open(
                path,
                "wb"
            ) as f:

                f.write(
                    r.content
                )



            images.append(
                filename
            )


            count+=1


            time.sleep(
                0.3
            )


        except Exception as e:

            print(
                "图片失败:",
                img_url,
                e
            )


    return images




# ======================
# 抓文章
# ======================

def parse_article(url):


    r=requests.get(
        url,
        headers=headers,
        timeout=15
    )


    r.encoding="utf-8"


    soup=BeautifulSoup(
        r.text,
        "lxml"
    )


    result={}


    result["url"]=url



    title=soup.select_one(
        "h3.entry-title a"
    )


    result["title"] = (
        title.get_text(strip=True)
        if title
        else ""
    )



    date=soup.select_one(
        "span.datespan"
    )


    result["date"] = (
        date.get_text(strip=True)
        if date
        else ""
    )



    date_key=format_date(
        result["date"]
    )


    main=soup.select_one(
        "div.main"
    )


    if main:


        for x in main.select(
            "script,style"
        ):

            x.decompose()



        result["content"]=main.get_text(
            "\n",
            strip=True
        )



        result["images"]=download_images(
            main,
            date_key
        )


    else:

        result["content"]=""

        result["images"]=[]



    return result





# ======================
# 主程序
# ======================


def main():


    all_links=set()



    print(
        "扫描文章列表..."
    )


    for page in tqdm(
        range(0,500)
    ):


        html=get_page(
            page
        )


        links=get_article_links(
            html
        )


        if not links:

            break


        all_links.update(
            links
        )


        time.sleep(
            0.5
        )



    print(
        "文章数量:",
        len(all_links)
    )



    articles=[]



    for url in tqdm(
        sorted(all_links)
    ):


        try:

            article=parse_article(
                url
            )


            articles.append(
                article
            )


            time.sleep(
                1
            )


        except Exception as e:

            print(
                "错误:",
                url,
                e
            )



    os.makedirs(
        "output",
        exist_ok=True
    )


    with open(
        OUTPUT,
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