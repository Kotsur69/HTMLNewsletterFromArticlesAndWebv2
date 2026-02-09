import requests
import json

# =====================
# KONFIGURACJA
# =====================
LM_STUDIO_URL = "http://localhost:1234"
MODEL_NAME = "liquid/lfm2-1.2b"

# =====================
# FUNKCJA TESTOWA CHAT
# =====================
def test_llm_chat():
    payload = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content": "Jesteś pomocnym asystentem."},
            {"role": "user", "content": "Napisz jedno zdanie po polsku o przemyśle stalowym."}
        ],
        "temperature": 0.2,
        "max_output_tokens": 2000
    }

    try:
        response = requests.post(f"{LM_STUDIO_URL}/v1/chat/completions", json=payload, timeout=30)
        response.raise_for_status()
        data = response.json()

        # LM Studio zwraca odpowiedź w data["choices"][0]["message"]["content"]
        choices = data.get("choices", [])
        if not choices:
            print("❌ Brak odpowiedzi w choices")
            return

        output_text = choices[0].get("message", {}).get("content", "")
        if not output_text:
            print("❌ LLM zwrócił pusty tekst")
        else:
            print("✅ LLM odpowiedź:")
            print(output_text)

    except Exception as e:
        print(f"❌ Błąd połączenia z LLM: {e}")


# =====================
# TEST
# =====================
if __name__ == "__main__":
    test_llm_chat()
