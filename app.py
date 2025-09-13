from flask import Flask, request, render_template, redirect, url_for, session
import os
import subprocess
import chromadb
from chromadb.utils import embedding_functions
from openai import OpenAI
from dotenv import load_dotenv
load_dotenv()
    
app = Flask(__name__)
app.secret_key = "srq-43814"  

client_llm = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

collection = None

@app.route("/", methods=["GET", "POST"])
def index():
    global collection
    message = None
    if request.method == "POST":
        repo_url = request.form["url"]

        try:
            repo_name = repo_url.rstrip('/').split('/')[-1]
            if repo_name.endswith('.git'):
                repo_name = repo_name[:-4]
            dest_dir = os.path.join(os.getcwd(), repo_name)

            # If dir already exists, clear it
            if os.path.exists(dest_dir):
                message = f"Repository already cloned at {dest_dir}"
            # Run git clone
            else:
                subprocess.run(["git", "clone", repo_url, dest_dir], check=True)
                message = f"Repository cloned successfully to {dest_dir}"
            collection = build_vector_index(dest_dir, persist_dir="repo_index")
            session["chat_history"] = []
            return redirect(url_for("query_page"))
        except subprocess.CalledProcessError:
            message = "Failed to clone repository. Please check the URL."
            return render_template("index.html", message=message)

    return render_template("index.html")

def build_vector_index(repo_path, persist_dir="repo_index"):
    ############### VECTOR DB SETUP ###############
    client = chromadb.PersistentClient(path=persist_dir)
    # client = chromadb.Client()

    openai_ef = embedding_functions.OpenAIEmbeddingFunction(
        api_key=os.environ.get("OPENAI_API_KEY"),
        model_name="text-embedding-3-small"
    )

    collection = client.get_or_create_collection(
        name="repo_files",
        embedding_function=openai_ef
    )

    file_extensions = (".py", ".js", ".ts", ".md")
    docs, ids, metas = [], [], []

    for root, _, files in os.walk(repo_path):
        for fname in files:
            if fname.endswith(file_extensions):
                file_path = os.path.join(root, fname)

                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        content = f.read()

                    if content.strip():
                        docs.append(content)
                        ids.append(file_path)  
                        metas.append({"path": file_path})

                except Exception as e:
                    print(f"Skipping {file_path}: {e}")

    if docs:
        collection.add(
            documents=docs,
            metadatas=metas,
            ids=ids
        )
        print(f"✅ Indexed {len(docs)} files from {repo_path}")
    else:
        print("⚠️ No matching files found")

    return collection

@app.route("/query", methods=["GET", "POST"])
def query_page():
    global collection
    answer = None

    if request.method == "POST":
        user_query = request.form["query"]

        if collection is None:
            answer = "❌ No repository indexed yet. Please clone one first."
        else:
            results = collection.query(query_texts=[user_query], n_results=3)

            retrieved_contexts = []
            for doc, meta in zip(results["documents"][0], results["metadatas"][0]):
                retrieved_contexts.append(f"File: {meta['path']}\n{doc}\n")

            context_text = "\n\n".join(retrieved_contexts)

            prompt = f"""
            You are a helpful assistant for understanding GitHub repositories. 
            Answer the user’s question based on the repo contents below.

            Context:
            {context_text}

            Question:
            {user_query}
            """

            response = client_llm.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.4,
            )

            answer = response.choices[0].message.content

        history = session["chat_history"]
        history.append({"role": "user", "content": user_query})
        history.append({"role": "assistant", "content": answer})
        session["chat_history"] = history
    return render_template("query.html", answer=answer, history=session.get("chat_history", []))


if __name__ == '__main__':
    app.run(debug=False)