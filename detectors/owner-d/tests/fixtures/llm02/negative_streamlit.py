# Synthetic LLM-02 negative: Streamlit reruns, but st.cache_data and session_state front the calls.
import streamlit as st
from openai import OpenAI

client = OpenAI()


@st.cache_data(ttl=3600)
def welcome():
    reply = client.chat.completions.create(
        model="gpt-4o-mini", temperature=0, messages=[{"role": "user", "content": "Write a two-line welcome."}]
    )
    return reply.choices[0].message.content


def tip():
    if "tip" not in st.session_state:
        st.session_state.tip = client.chat.completions.create(
            model="gpt-4o-mini", temperature=0, messages=[{"role": "user", "content": "Give one usage tip."}]
        )
    return st.session_state.tip


st.write(welcome())
st.write(tip())
