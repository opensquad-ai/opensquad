"""Minimal Bing SERP HTML fixture with weather answer card + organic links."""

FIXTURE_WEATHER_SERP_HTML = """
<!DOCTYPE html>
<html>
<body>
  <div id="b_content">
    <ol id="b_results">
      <li class="b_ans">
        <div id="wtr_module" class="wtr_module">
          <h2>福州</h2>
          <div class="wtr_curr">
            <a href="https://www.msn.com/zh-cn/weather/forecast/in-Fuzhou">MSN 天气</a>
            <span>34℃</span>
            <span>晴</span>
            <span>东北风 1 级</span>
          </div>
          <div class="wtr_forecast">未来几天：多云 33℃ / 26℃，阵雨 31℃ / 25℃</div>
        </div>
      </li>
      <li class="b_algo">
        <h2><a href="https://baike.baidu.com/item/%E7%A6%8F%E5%B7%9E%E5%B8%82/366603">福州市_百度百科</a></h2>
        <div class="b_caption"><p>福州市地貌属典型的河口盆地，属亚热带季风气候。</p></div>
      </li>
      <li class="b_algo">
        <h2><a href="https://www.thepaper.cn/newsDetail_forward_26004317">福州10大好玩景点</a></h2>
        <div class="b_caption"><p>福州好玩的景点有：三坊七巷，平潭岛，福州国家森林公园</p></div>
      </li>
      <li class="b_algo">
        <h2><a href="https://www.tianqihoubao.com/weather/fuzhou/20260713.htm">7月13日福州天气_天气预报</a></h2>
        <div class="b_caption"><p>查询福州2026年7月13日的历史天气与预报</p></div>
      </li>
    </ol>
  </div>
</body>
</html>
"""

FIXTURE_RELATED_ONLY_HTML = """
<!DOCTYPE html>
<html>
<body>
  <div id="b_content">
    <ol id="b_results">
      <li class="b_ans b_rs">
        <div class="b_rs">相关搜索</div>
        <ul><li>福州旅游</li><li>福州美食</li></ul>
      </li>
      <li class="b_algo">
        <h2><a href="https://example.com/a">普通结果</a></h2>
        <div class="b_caption"><p>普通摘要内容足够长</p></div>
      </li>
    </ol>
  </div>
</body>
</html>
"""
