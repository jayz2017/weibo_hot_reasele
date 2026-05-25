# weibo_hot_reasele

爬取微博热榜
热榜地址为：https://weibo.com/ajax/side/hotSearch

热榜地址内容：json格式，包含以下字段：icon_desc、word、num、icon、small_icon_desc、rank
icon_desc：热词类型，例如：新、热、新热、新热等
word：热词内容
num：热词出现次数
icon：热词图标
small_icon_desc：热词图标描述
rank：热词排名
示例：
icon_desc
            {
                "icon_width": 24,
                "icon_desc": "新",
                "emoticon": "",
                "icon_desc_color": "#ff3852",
                "label_name": "新",
                "topic_flag": 1,
                "small_icon_desc_color": "#ff3852",
                "word_scheme": "#曝iPhone18Pro配色大换血#",
                "note": "曝iPhone18Pro配色大换血",
                "num": 1311905,
                "icon_height": 24,
                "word": "曝iPhone18Pro配色大换血",
                "realpos": 1,
                "icon": "https://simg.s.weibo.com/moter/flags/1_0.png",
                "flag": 1,
                "small_icon_desc": "新",
                "rank": 0
            }
 过滤：对热词类型进行过滤，对于政府、政治、市政、时政、相关热词不做过滤保留，只留存科技、娱乐、花边、体育   ，热词类型为icon_desc   为新、热、新热、新热   
第二步：获取热榜关键词下的内容，使用https://s.weibo.com/weibo?q=#关键词#       
第三步，将获取到的热榜关键词下的内容，进行处理，将文章内容的文本进行读取，存储，还有就是文章内容部分连同区域作者的名称以及截取图片进行保存
第四步，将文章内容点击打开之后，对文章所属的评论区内容进行处理，将评论区的文本进行读取，存储，还有就是评论区的作者名称以及截取图片进行保存



