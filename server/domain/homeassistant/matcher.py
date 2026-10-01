"""
Fuzzy Entity Matcher & Intent Parser for Home Assistant (Domain: HomeAssistant).
Parses Russian voice commands, normalizes grammatical inflections (declensions/cases),
and fuzzy matches entities against Home Assistant state list.
"""
import re
import logging
from typing import Dict, Any, Optional, List, Tuple

logger = logging.getLogger("FuzzyEntityMatcher")


# Action definitions and trigger verbs in Russian & English
ACTION_MAP = {
    "turn_on": [
        "включи", "включить", "вруби", "врубить", "запусти", "запустить",
        "зажги", "зажечь", "открой", "открыть", "активируй", "активировать",
        "подними", "поднять", "вкл", "turn_on", "turn on", "on", "start"
    ],
    "turn_off": [
        "выключи", "выключить", "выруби", "вырубить", "погаси", "погасить",
        "отключи", "отключить", "закрой", "закрыть", "останови", "остановить",
        "деактивируй", "деактивировать", "опусти", "опустить", "выкл",
        "turn_off", "turn off", "off", "stop"
    ],
    "toggle": [
        "переключи", "переключить", "смени", "поменяй", "toggle"
    ]
}

# Read-only domains that cannot be controlled via turn_on/turn_off
READONLY_DOMAINS = {
    "binary_sensor", "sensor", "device_tracker", "person", "zone",
    "sun", "weather", "update", "camera", "image", "stt", "tts", "conversation"
}

# Domain keyword mappings for domain boosting
DOMAIN_KEYWORDS = {
    "fan": ["вытяжка", "вытяжку", "вытяжки", "вытяжкой", "вентилятор", "вентиляцию", "вентиляция", "обдув", "кулер", "fan", "extractor", "exhaust"],
    "light": ["свет", "освещение", "люстра", "люстру", "лампа", "лампу", "лампочка", "лампочку", "бра", "подсветка", "подсветку", "светильник", "light"],
    "switch": ["розетка", "розетку", "выключатель", "переключатель", "прибор", "switch", "outlet"],
    "climate": ["кондиционер", "кондей", "обогреватель", "отопление", "климат", "термостат", "heat", "ac", "climate"],
    "cover": ["шторы", "штору", "жалюзи", "рольставни", "ворота", "blinds", "cover", "curtain"]
}

# Stop words to ignore during matching
STOP_WORDS = {"в", "на", "из", "над", "под", "пожалуйста", "мне", "свой", "своей", "и", "или", "для", "де"}
PREPOSITIONS = {"в", "во", "на", "над", "под", "у", "возле", "около", "за", "перед", "с", "со", "из", "по", "для", "от"}


def stem_russian_word(word: str) -> str:
    """
    Lightweight Russian stemming / suffix normalization for entity matching.
    Strips case endings (nominative, accusative, prepositional, etc.).
    """
    w = word.lower().strip()
    if len(w) <= 3:
        return w

    # Strip multi-letter suffixes
    suffixes = [
        "ами", "ями", "ого", "его", "ому", "ему", "ыми", "ими", "ов", "ев", "ей",
        "ам", "ям", "ах", "ях", "ом", "ем", "ая", "яя", "ое", "ее", "ые", "ие",
        "ую", "юю", "ой", "ей", "ых", "их"
    ]
    for suf in suffixes:
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            return w[:-len(suf)]

    # Strip single-letter suffixes
    single_suffixes = ["а", "я", "у", "ю", "е", "ы", "и", "о", "й"]
    for suf in single_suffixes:
        if w.endswith(suf) and len(w) - 1 >= 3:
            return w[:-1]

    return w


def extract_action(text: str) -> Tuple[str, str]:
    """
    Extract action (turn_on, turn_off, toggle) and clean target command text.
    """
    lower_text = text.lower().strip()
    found_action = None
    target_text = lower_text

    for action, keywords in ACTION_MAP.items():
        for kw in keywords:
            pattern = r'\b' + re.escape(kw) + r'\b'
            if re.search(pattern, lower_text):
                found_action = action
                target_text = re.sub(pattern, '', lower_text).strip()
                break
        if found_action:
            break

    target_text = re.sub(r'\s+', ' ', target_text).strip()
    return found_action or "turn_on", target_text


def tokenize_and_stem(text: str) -> List[str]:
    """
    Tokenize text into words, remove punctuation and stop words, stem Russian words.
    """
    words = re.findall(r'[a-zA-Zа-яА-Я0-9]+', text.lower())
    stems = []
    for w in words:
        if w not in STOP_WORDS:
            stems.append(stem_russian_word(w))
    return stems


def is_device_word(word: str) -> bool:
    """Check if a word (or its stem) corresponds to a known HA domain keyword."""
    w_stem = stem_russian_word(word.lower())
    for domain, kws in DOMAIN_KEYWORDS.items():
        for kw in kws:
            if stem_russian_word(kw) == w_stem:
                return True
    return False


def parse_complex_command(text: str) -> List[Tuple[str, str]]:
    """
    Parses complex natural language voice commands into sub-commands.
    Handles multiple actions, shared base devices with distributed qualifiers/locations.

    Examples:
        - "включи свет над кроватью и над столом" ->
          [("turn_on", "свет над кроватью"), ("turn_on", "свет над столом")]
        - "включи вытяжку на кухне и в туалете" ->
          [("turn_on", "вытяжку на кухне"), ("turn_on", "вытяжку в туалете")]
        - "включи свет на кухне и выключи вытяжку в туалете" ->
          [("turn_on", "свет на кухне"), ("turn_off", "вытяжку в туалете")]
    """
    lower_text = text.lower().strip()

    # Step 1: Detect action verb positions
    action_matches = []
    for action, keywords in ACTION_MAP.items():
        for kw in keywords:
            pattern = r'\b' + re.escape(kw) + r'\b'
            for m in re.finditer(pattern, lower_text):
                action_matches.append((m.start(), m.end(), action, m.group(0)))

    action_matches.sort(key=lambda x: x[0])

    # Remove overlapping action matches
    filtered_action_matches = []
    for match in action_matches:
        if not filtered_action_matches or match[0] >= filtered_action_matches[-1][1]:
            filtered_action_matches.append(match)

    # Step 2: Split text into clauses if multiple distinct actions exist
    clauses = []
    if len(filtered_action_matches) > 1:
        for i, match in enumerate(filtered_action_matches):
            start_pos = match[0]
            end_pos = filtered_action_matches[i+1][0] if i + 1 < len(filtered_action_matches) else len(lower_text)
            clause_text = lower_text[start_pos:end_pos].strip()
            clause_text = re.sub(r'[\s,]+(?:и|а|затем|потом|а\s+также)+$', '', clause_text).strip()
            if clause_text:
                act, target_phrase = extract_action(clause_text)
                clauses.append((act, target_phrase))
    else:
        act, target_phrase = extract_action(lower_text)
        clauses.append((act, target_phrase))

    # Step 3: For each clause, expand sub-target phrases
    sub_commands = []
    for action, target_phrase in clauses:
        if not target_phrase:
            sub_commands.append((action, text))
            continue

        raw_segments = [s.strip() for s in re.split(r'\s*(?:,\s*|\s+(?:и|а\s+также|или)\s+)', target_phrase) if s.strip()]
        if len(raw_segments) <= 1:
            sub_commands.append((action, target_phrase))
            continue

        current_device_words = ""
        prev_location_suffix = ""

        seg0 = raw_segments[0]
        seg0_words = seg0.split()

        first_prep_idx = None
        for idx, w in enumerate(seg0_words):
            if w.lower() in PREPOSITIONS:
                first_prep_idx = idx
                break

        if first_prep_idx is not None and first_prep_idx > 0:
            current_device_words = " ".join(seg0_words[:first_prep_idx])
            prev_location_suffix = " ".join(seg0_words[first_prep_idx:])
        else:
            if any(is_device_word(w) for w in seg0_words):
                current_device_words = seg0

        expanded_targets = []
        for idx, seg in enumerate(raw_segments):
            words = seg.split()
            if not words:
                continue

            first_w = words[0].lower()
            has_device_word = any(is_device_word(w) for w in words)
            starts_with_prep = first_w in PREPOSITIONS

            if idx == 0:
                expanded_targets.append(seg)
                continue

            if starts_with_prep:
                if current_device_words:
                    expanded = f"{current_device_words} {seg}"
                else:
                    expanded = seg
            elif has_device_word:
                prep_idx = None
                for i, w in enumerate(words):
                    if w.lower() in PREPOSITIONS:
                        prep_idx = i
                        break
                if prep_idx is not None and prep_idx > 0:
                    current_device_words = " ".join(words[:prep_idx])
                else:
                    current_device_words = seg
                expanded = seg
            else:
                if current_device_words:
                    prep_word = prev_location_suffix.split()[0] if prev_location_suffix else ""
                    if prep_word and prep_word in PREPOSITIONS:
                        expanded = f"{current_device_words} {prep_word} {seg}"
                    else:
                        expanded = f"{current_device_words} {seg}"
                else:
                    expanded = seg

            expanded_targets.append(expanded)

        for target in expanded_targets:
            sub_commands.append((action, target))

    return sub_commands if sub_commands else [extract_action(text)]


class FuzzyEntityMatcher:
    """
    Fuzzy Entity Matcher for Home Assistant.
    Matches natural language text against Home Assistant entity list.
    """

    @staticmethod
    def match_entities(
        text: str,
        entities: List[Dict[str, Any]],
        area_id: str = ""
    ) -> List[Dict[str, Any]]:
        """
        Find matching HA entities for single or complex multi-device voice commands.

        Returns:
            List of Dicts containing entity_id, domain, action, friendly_name, score, speech_response.
        """
        sub_commands = parse_complex_command(text)
        matched_results = []
        seen_entity_ids = set()

        has_voice_control_flag = any(
            "voice_control" in e.get("attributes", {}) for e in entities
        )

        for action, target_phrase in sub_commands:
            cmd_stems = tokenize_and_stem(target_phrase)
            if not cmd_stems:
                cmd_stems = tokenize_and_stem(text)

            best_entity = None
            best_score = 0.0

            for entity in entities:
                entity_id = entity.get("entity_id", "")
                if not entity_id or "." not in entity_id:
                    continue

                if entity_id in seen_entity_ids:
                    continue

                domain = entity_id.split(".")[0]
                if domain in READONLY_DOMAINS:
                    continue

                attributes = entity.get("attributes", {})
                voice_control_val = attributes.get("voice_control")
                if voice_control_val is False or str(voice_control_val).lower() in ("false", "0", "no"):
                    continue

                if has_voice_control_flag:
                    is_enabled = voice_control_val is True or str(voice_control_val).lower() in ("true", "1", "yes")
                    if not is_enabled:
                        continue

                friendly_name = attributes.get("friendly_name", entity_id)
                entity_texts = [friendly_name.lower(), entity_id.lower().replace(".", " ").replace("_", " ")]
                aliases = attributes.get("aliases", [])
                if isinstance(aliases, list):
                    for alias in aliases:
                        if isinstance(alias, str):
                            entity_texts.append(alias.lower())

                entity_stems = []
                for et in entity_texts:
                    entity_stems.extend(tokenize_and_stem(et))
                entity_stems = list(set(entity_stems))

                if not entity_stems:
                    continue

                matched_stems = [s for s in cmd_stems if s in entity_stems or any(s in es or es in s for es in entity_stems)]
                overlap_score = len(matched_stems) / max(len(cmd_stems), 1)

                domain_bonus = 0.0
                for kw in DOMAIN_KEYWORDS.get(domain, []):
                    kw_stem = stem_russian_word(kw)
                    if kw_stem in cmd_stems or any(kw_stem in s for s in cmd_stems):
                        domain_bonus = 0.25
                        break

                area_bonus = 0.0
                if area_id and (area_id.lower() in entity_id.lower() or area_id.lower() in friendly_name.lower()):
                    area_bonus = 0.15

                total_score = (overlap_score * 0.6) + domain_bonus + area_bonus

                if total_score > best_score and (len(matched_stems) > 0 or total_score > 0.5):
                    best_score = total_score
                    service_domain = "homeassistant" if domain in {"fan", "light", "switch", "climate", "input_boolean", "automation", "script"} else domain

                    best_entity = {
                        "entity_id": entity_id,
                        "domain": service_domain,
                        "target_domain": domain,
                        "action": action,
                        "friendly_name": friendly_name,
                        "score": total_score
                    }

            if best_entity and best_score >= 0.35:
                seen_entity_ids.add(best_entity["entity_id"])
                fname = best_entity["friendly_name"]
                domain = best_entity.get("target_domain", best_entity["domain"])
                act = best_entity["action"]

                fname_lower = fname.lower()
                if act == "turn_on":
                    if "вытяжк" in fname_lower or "вентилятор" in fname_lower or domain == "fan":
                        speech = f"{fname} включена"
                    elif "люстр" in fname_lower or "ламп" in fname_lower or "подсветк" in fname_lower or "розетк" in fname_lower:
                        speech = f"{fname} включена"
                    elif "свет" in fname_lower or domain == "light":
                        speech = f"{fname} включен"
                    else:
                        speech = f"{fname} включено"
                elif act == "turn_off":
                    if "вытяжк" in fname_lower or "вентилятор" in fname_lower or domain == "fan":
                        speech = f"{fname} выключена"
                    elif "люстр" in fname_lower or "ламп" in fname_lower or "подсветк" in fname_lower or "розетк" in fname_lower:
                        speech = f"{fname} выключена"
                    elif "свет" in fname_lower or domain == "light":
                        speech = f"{fname} выключен"
                    else:
                        speech = f"{fname} выключено"
                else:
                    speech = f"{fname} переключено"

                best_entity["speech_response"] = speech
                matched_results.append(best_entity)

        if matched_results:
            combined_speech = FuzzyEntityMatcher.format_combined_speech(matched_results)
            for res in matched_results:
                res["combined_speech_response"] = combined_speech

        return matched_results

    @staticmethod
    def match_entity(
        text: str,
        entities: List[Dict[str, Any]],
        area_id: str = ""
    ) -> Optional[Dict[str, Any]]:
        """
        Find the best matching HA entity for a given command text.
        Maintains backwards compatibility with single entity caller expectations.
        """
        matches = FuzzyEntityMatcher.match_entities(text, entities, area_id)
        if matches:
            res = dict(matches[0])
            res["speech_response"] = FuzzyEntityMatcher.format_combined_speech(matches)
            return res
        return None

    @staticmethod
    def format_combined_speech(matched_entities: List[Dict[str, Any]]) -> str:
        """Format natural language speech response for one or more matched entities."""
        if not matched_entities:
            return ""
        if len(matched_entities) == 1:
            return matched_entities[0].get("speech_response", "")

        actions_set = {e["action"] for e in matched_entities}
        if len(actions_set) == 1:
            act = list(actions_set)[0]
            fnames = [e["friendly_name"] for e in matched_entities]
            if len(fnames) == 2:
                names_str = f"{fnames[0]} и {fnames[1]}"
            else:
                names_str = ", ".join(fnames[:-1]) + f" и {fnames[-1]}"

            if act == "turn_on":
                return f"{names_str} включены"
            elif act == "turn_off":
                return f"{names_str} выключены"
            else:
                return f"{names_str} переключены"
        else:
            speeches = [e.get("speech_response", f"{e['friendly_name']} обработано") for e in matched_entities]
            return ", а ".join(speeches)

