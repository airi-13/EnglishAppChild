import os

from app import create_app

app = create_app()

if __name__ == "__main__":
    # 開発・確認用。本番は gunicorn を使う（README参照）
    app.run(host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "5050")), debug=False, threaded=True)
