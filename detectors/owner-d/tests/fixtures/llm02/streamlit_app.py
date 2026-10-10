# Synthetic LLM-02 positive: a Streamlit script reruns top to bottom on every interaction.
import streamlit as st
from openai import OpenAI

client = OpenAI()

st.title("Daily briefing")
question = st.text_input("Ask a question")

intro = client.chat.completions.create(
    model="gpt-4o-mini",
    temperature=0,
    messages=[{"role": "user", "content": "Write a two-line welcome for the briefing page."}],
)
st.write(intro.choices[0].message.content)

if question:
    answer = client.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "user", "content": question}])
    st.write(answer.choices[0].message.content)
