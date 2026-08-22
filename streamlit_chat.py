import streamlit as st
import time

# Mock LLM response generator
# Replace or extend this with your actual LLM call

def generate_llm_response(user_input: str) -> str:
    # Simulate processing delay
    time.sleep(1)
    return f"Echo: {user_input}"


# Initialize session state variables
if 'history' not in st.session_state:
    st.session_state.history = []  # list of (user, bot) tuples

if 'waiting_confirmation' not in st.session_state:
    st.session_state.waiting_confirmation = False

if 'pending_response' not in st.session_state:
    st.session_state.pending_response = None


st.title("Explicit Confirmation Chat Interface")

# Display chat history
for i, (user_msg, bot_msg) in enumerate(st.session_state.history):
    st.markdown(f"**User:** {user_msg}")
    st.markdown(f"**Bot:** {bot_msg}")


# User input field
if not st.session_state.waiting_confirmation:
    user_input = st.text_input("Your message:", key='user_input')
else:
    user_input = None


# Handle user input
if user_input and not st.session_state.waiting_confirmation:
    # Generate LLM response but do not add immediately
    st.session_state.pending_response = generate_llm_response(user_input)
    st.session_state.current_user_input = user_input
    st.session_state.waiting_confirmation = True


# Show pending LLM response and confirm button
if st.session_state.waiting_confirmation and st.session_state.pending_response is not None:
    st.markdown("---")
    st.markdown(f"**LLM proposed response:** {st.session_state.pending_response}")
    if st.button("Confirm"):
        # On confirmation, add conversation to history
        st.session_state.history.append((st.session_state.current_user_input, st.session_state.pending_response))
        # Clear states
        st.session_state.pending_response = None
        st.session_state.current_user_input = None
        st.session_state.waiting_confirmation = False

    if st.button("Reject"):
        # Clear states without appending
        st.session_state.pending_response = None
        st.session_state.current_user_input = None
        st.session_state.waiting_confirmation = False
        st.experimental_rerun()


# Scroll to bottom
st.write("""
<style>
div[data-testid="stVerticalBlock"] > div:last-child {
    scroll-margin-bottom: 20px;
}
</style>
""", unsafe_allow_html=True)
