import os
import sys

# Ensure src is in path
sys.path.append(os.path.join(os.getcwd(), "src"))

from orchestrator.skill_loader import SkillLoader


def test_loader():
    print("Testing Skill Loader...")
    loader = SkillLoader(skills_dir="skills")
    skills = loader.load_skills()

    if "hello" in skills:
        print("✅ Skill 'hello' loaded successfully.")
        skill = skills["hello"]
        print(f"   Name: {skill.name}")
        print(f"   Description: {skill.description}")
        print(f"   Inputs: {skill.inputs}")
    else:
        print("❌ Skill 'hello' NOT found.")
        print(f"Loaded skills: {list(skills.keys())}")


if __name__ == "__main__":
    test_loader()
