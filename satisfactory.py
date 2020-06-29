import json
import math
import copy
from datetime import datetime

recipes = {}
projects = {}
buildings = {}
log_file = open('satisfactory.log', 'w')


def add_ingredients(product, quantity, flat_requirements):
    produced = recipes[product].get("produced", 1)
    for ingredient in recipes[product].get("ingredients", {}):
        ingredient_name = ingredient["name"]
        ingredient_quantity = ingredient["quantity"] * quantity / produced
        flat_requirements[ingredient_name] = flat_requirements.get(ingredient_name, 0) + ingredient_quantity
        add_ingredients(ingredient_name, ingredient_quantity, flat_requirements)


def gather_project_requirements(project, requirements):
    for requirement in project["requirements"]:
        product = requirement["name"]
        quantity = requirement["quantity"]
        requirements[product] = requirements.get(product, 0) + quantity
        add_ingredients(product, quantity, requirements)


def compute_power_requirements(requirements):
    power = 0
    for product, quantity in requirements.items():
        recipe = recipes[product]
        building = buildings[recipe["building"]]
        power += building["power"] * 60.0 * quantity / recipe["rate"]
    return power


def gather_power_requirements_projects(projects_list):
    requirements = {}
    for project_name in projects_list:
        project = projects[project_name]
        gather_project_requirements(project, requirements)
    return math.ceil(compute_power_requirements(requirements))


def ftime(secs):
    if secs < 60:
        return f"{secs}s"
    else:
        return f"{math.floor(secs / 60)}:{secs % 60:0>2d}"


def log(s):
    print(s, file=log_file)


class FactoryConstraints:
    conveyor_speed = 0
    max_time = 0
    max_buildings = {}


class FactorySolution:
    name = ''
    automation_time = 0
    handcrafting_time = 0
    total_time = 0
    machine_count = 0
    constructor_count = 0
    machines = {}
    automation_times = {}
    automation_production = {}
    handcrafting_times = {}
    handcrafting_production = {}
    handcrafting_order = []

    def __copy__(self):
        cpy = FactorySolution()
        cpy.handcrafting_time = self.handcrafting_time
        cpy.automation_time = self.automation_time
        cpy.total_time = self.total_time
        cpy.machine_count = self.machine_count
        cpy.constructor_count = self.constructor_count
        cpy.machines = self.machines.copy()
        cpy.automation_times = self.automation_times.copy()
        cpy.handcrafting_times = self.handcrafting_times.copy()
        cpy.automation_production = self.automation_production.copy()
        cpy.handcrafting_production = self.handcrafting_production.copy()
        cpy.handcrafting_order = self.handcrafting_order.copy()
        return cpy
    
    def compute_derived_values(self):
        self.automation_time = max(self.automation_times.values(), default=0)
        self.handcrafting_time = sum(self.handcrafting_times.values())
        self.total_time = max(self.automation_time, self.handcrafting_time)
        self.machine_count = sum(self.machines.values())
        self.constructor_count = sum({count for name, count in self.machines.items() if recipes[name]["building"] == "Constructor"})
    
    def log_machines(self, requirements):
        log('')
        log('Machines allocated:')
        for product, count in self.machines.items():
            log(f'{count} {product} {ftime(self.automation_times[product])} ({requirements[product]} @ {count} * {recipes[product]["rate"]}/min)')
        log(f'Total automation time {ftime(self.automation_time)}')
        log(f'{self.constructor_count} constructors')
        log(f'{self.machine_count} machines')

    def evaluate_solution_time(self, requirements, constraints):
        self.handcrafting_times = {}
        self.handcrafting_production = {}
        self.automation_times = {}
        self.automation_production = {}
        self.handcrafting_time = 0
        self.automation_time = 0
        self.handcrafting_order = []
        for product, quantity in requirements.items():
            recipe = recipes[product]
            machine_count = self.machines[product]
            if machine_count == 0:
                time = math.ceil(quantity / recipe.get("produced", 1) * recipe["build_steps"] * 0.45)
                self.handcrafting_times[product] = time
                self.handcrafting_time += time
                self.handcrafting_production[product] = quantity
                self.handcrafting_order.append(product)
            else:
                time = math.ceil(60.0 * quantity / (machine_count * min(constraints.conveyor_speed, recipe["rate"])))
                self.automation_times[product] = time
                self.automation_time = max(self.automation_time, time)
                self.automation_production[product] = quantity

        self.total_time = max(self.handcrafting_time, self.automation_time)

    def print_times(self):
        log(f"Solution time ===> {ftime(self.total_time)}, automation {ftime(self.automation_time)}, manual {ftime(self.handcrafting_time)} <===")


class FactorySolverBase:
    constraints = FactoryConstraints()
    requirements = {}

    def report_results(self, solutions):
        log("")
        log("Best times per constructor count:")
        for index, solution in enumerate(solutions):
            if index == 0 or (solution.constructor_count > solutions[index - 1].constructor_count and solution.total_time < solutions[index - 1].total_time):
                constructor_counts = {name: count for name, count in solution.machines.items() if recipes[name]["building"] == "Constructor" and count > 0}
                handcrafting_times = {f"{name}: {ftime(time)}" for name, time in solution.handcrafting_times.items()}
                log(f"{solution.constructor_count} machines: {ftime(solution.total_time)} hand {ftime(solution.handcrafting_time)} " f"{constructor_counts} {handcrafting_times}")

    def optimize_machines(self, project_name, constraints):
        log('')
        log(f"Computing optimal machine configuration for {project_name}")
        self.constraints = constraints
        project = projects[project_name]
        gather_project_requirements(project, self.requirements)

        log(f"Requires:")
        for product, quantity in self.requirements.items():
            log(f"{quantity:.0f} {product}")
        self.solve()

    def solve(self):
        raise NotImplementedError()


class FactorySolver(FactorySolverBase):
    def solve(self):
        # initial solution - start by hand crafting everything that is craftable
        solution = FactorySolution()
        for product in self.requirements.keys():
            recipe = recipes[product]
            if recipe.get("build_steps", 0) == 0:
                solution.machines[product] = 1
            else:
                solution.machines[product] = 0

        log("")
        log("Evaluating initial solution:")
        solution.evaluate_solution_time(self.requirements, self.constraints)
        solution = self.optimize_handcrafting(solution)
        solution.print_times()

        solutions = [solution]

        while solutions[-1].total_time > 60 and solutions[-1].constructor_count < 200:
            log("Starting iteration")
            # try every product
            best_candidate = copy.copy(solutions[-1])
            best_candidate_product = ""
            best_handcrafting_time_saved = 0
            best_automation_time_saved = 0
            for product in self.requirements.keys():
                candidate_solution = copy.copy(solutions[-1])

                if candidate_solution.machines[product] >= self.constraints.max_buildings.get(product, 1000):
                    continue

                candidate_solution.machines[product] += 1
                candidate_solution.machine_count += 1
                if recipes[product]["building"] == "Constructor":
                    candidate_solution.constructor_count += 1

                log("")
                log(f"Candidate solution: {product}, {candidate_solution.machines[product]} machines")

                candidate_solution.evaluate_solution_time(self.requirements, self.constraints)
                candidate_solution = self.optimize_handcrafting(candidate_solution)

                blockers = set()
                best_blockers = set()
                for prod, value in candidate_solution.automation_times.items():
                    if value == candidate_solution.total_time:
                        blockers |= {prod}
                for prod, value in best_candidate.automation_times.items():
                    if value == best_candidate.total_time:
                        best_blockers |= {prod}

                candidate_solution.print_times()
                handcrafting_delta = best_candidate.handcrafting_times.get(product, 0) - candidate_solution.handcrafting_times.get(product, 0)
                log(
                    f"Time delta {product} automation {ftime(best_candidate.automation_times.get(product, 0))} -> "
                    f"{ftime(candidate_solution.automation_times[product])} "
                    f"manual {ftime(best_candidate.handcrafting_times.get(product, 0))} -> "
                    f"{ftime(candidate_solution.handcrafting_times.get(product, 0))} ({ftime(handcrafting_delta)} saved)"
                )
                log(f"Blockers: {blockers}")
                handcrafted_products = [(product, ftime(candidate_solution.handcrafting_times[product])) for product in candidate_solution.handcrafting_order]
                log(f"Handcrafting: {handcrafted_products}.")
                better = False
                reason = ""
                if candidate_solution.total_time < best_candidate.total_time:
                    better = True
                    reason = f"shorter time {ftime(candidate_solution.total_time)} vs {ftime(best_candidate.total_time)}"
                elif candidate_solution.total_time == best_candidate.total_time:
                    # in case of identical times, go for the option with fewer blockers, but only if the product we're choosing is one of the blockers.
                    if product in best_blockers and not best_candidate_product in best_blockers:
                        better = True
                        reason = f"fewer blockers ({blockers} vs {best_blockers}"
                    elif len(blockers) == len(best_blockers):
                        # if the blockers are the same then go for the option that is more efficient in terms of handcrafting time.
                        if candidate_solution.handcrafting_time < best_candidate.handcrafting_time:
                            better = True
                            reason = f"shorter handcrafting ({ftime(candidate_solution.handcrafting_time)} vs {ftime(best_candidate.handcrafting_time)})"
                        elif candidate_solution.handcrafting_time == best_candidate.handcrafting_time:
                            handcrafting_time_saved = best_candidate.handcrafting_times.get(product, 0) - candidate_solution.handcrafting_times.get(product, 0)
                            if handcrafting_time_saved > best_handcrafting_time_saved:
                                better = True
                                reason = f"more handcraft time saved {ftime(handcrafting_time_saved)} vs {ftime(best_handcrafting_time_saved)})"
                            elif handcrafting_time_saved == best_handcrafting_time_saved:
                                automation_time_saved = best_candidate.automation_times.get(product, 0) - candidate_solution.automation_times.get(product, 0)
                                if automation_time_saved > best_automation_time_saved:
                                    better = True
                                    reason = f"more automation time saved {ftime(automation_time_saved)} vs {ftime(best_automation_time_saved)})"
                                elif automation_time_saved == best_automation_time_saved:
                                    if candidate_solution.constructor_count < best_candidate.constructor_count:
                                        better = True
                                        reason = f"fewer constructors"
                                    elif candidate_solution.constructor_count == best_candidate.constructor_count:
                                        # if comparing to the previous step's solution, so clearly not better if it's just adding a machine with no effect.
                                        if candidate_solution.machine_count == best_candidate.machine_count:
                                            if best_candidate_product == "Concrete":
                                                # better = True
                                                reason = f"deprioritizing Concrete"
                                            log(f"Candidate {product} is a tie")

                if better:
                    log(f"Candidate {product} is better than previous solution because of {reason}")
                    best_handcrafting_time_saved = best_candidate.handcrafting_times.get(product, 0) - candidate_solution.handcrafting_times.get(product, 0)
                    best_automation_time_saved = best_candidate.automation_times.get(product, 0) - candidate_solution.automation_times.get(product, 0)
                    best_candidate = candidate_solution
                    best_candidate_product = product

            log("")
            log("Winning candidate:")
            best_candidate.print_times()
            solutions.append(best_candidate)
            if best_candidate_product != "":
                building = recipes[best_candidate_product]["building"]
                log(f"Outcome: Adding one {building} ({best_candidate_product}), now {best_candidate.machines[best_candidate_product]} machines")
                log("")
            else:
                break

        self.report_results(solutions)
        return solutions

    def allocate_remaining_handcrafting_time(self, solution, product, requirements):
        # handcraft for any remaining time
        recipe = recipes[product]
        leftover = solution.automation_time - solution.handcrafting_time
        if leftover <= 0 or recipe.get("build_steps", 0) == 0 or solution.handcrafting_times.get(product, 0) > 0:
            return

        already_produced = math.floor(solution.handcrafting_time * solution.machines[product] * recipe["rate"] / 60.0)
        to_produce = requirements[product] - already_produced
        if to_produce <= 0:
            return

        # n machines at speed s1 and 1 machine (hand craft) at speed s2
        # how much time t will it take to complete x products?
        # t(n * s1 + s2) = x
        # t = x / (n * s1 + s2)
        time_spent_handcrafting = math.ceil(60.0 * to_produce / (solution.machines[product] * recipe["rate"] + recipe.get("produced", 1) * 60.0 / (0.45 * recipe["build_steps"])))

        solution.automation_times[product] = solution.handcrafting_time + time_spent_handcrafting
        solution.handcrafting_times[product] = time_spent_handcrafting
        solution.handcrafting_time += time_spent_handcrafting
        solution.automation_production[product] = math.floor(already_produced + time_spent_handcrafting / 60.0 * solution.machines[product] * recipe["rate"])
        solution.handcrafting_production[product] = math.floor(recipe.get("produced", 1) * time_spent_handcrafting / (0.45 * recipe["build_steps"]))

        solution.automation_time = 0
        for time in solution.automation_times.values():
            self.automation_time = max(time, solution.automation_time)
        solution.total_time = max(solution.automation_time, solution.handcrafting_time)

    def optimize_handcrafting(self, solution):
        log(f"Initial handcrafting time {ftime(solution.handcrafting_time)}, automation time {ftime(solution.automation_time)}")
        log(f"Crafting {solution.handcrafting_order}")
        if solution.handcrafting_time > solution.automation_time:
            return solution

        # use up any left over hand crafting time until there is no left over time.
        prev_solution = solution
        while True:
            # try handcrafting every possible product, choose the one with the best improvement
            # classify products by the advantage that crafting offers vs. automation
            # avoid going to a less advantgeous class if higher levered classes are available
            candidate_products = []
            best_ratio = 0.0
            for product in prev_solution.machines.keys():
                recipe = recipes[product]
                if recipe.get("build_steps", 0) == 0:
                    continue
                if prev_solution.handcrafting_times.get(product, 0) > 0:
                    continue
                ratio = recipe.get("produced", 1) * 60.0 / (0.45 * recipe["build_steps"]) / recipe["rate"]
                if abs(ratio - best_ratio) < 0.01:
                    candidate_products.append(product)
                elif ratio > best_ratio:
                    candidate_products = [product]
                    best_ratio = ratio

            # for product in solution.machines.keys():
            best_solution = prev_solution
            for product in candidate_products:
                candidate_solution = copy.copy(prev_solution)
                candidate_solution.handcrafting_order.append(product)
                self.allocate_remaining_handcrafting_time(candidate_solution, product, self.requirements)
                if candidate_solution.total_time < best_solution.total_time:
                    best_solution = candidate_solution

            if best_solution != prev_solution:
                best_product = best_solution.handcrafting_order[-1]
                log(
                    f"Hand crafting {best_product} for {ftime(best_solution.handcrafting_times[best_product])} "
                    f"total time {ftime(best_solution.total_time)} vs {ftime(prev_solution.total_time)}, "
                    f"manual time now {ftime(best_solution.handcrafting_time)}"
                )
                prev_solution = best_solution
            else:
                break
        return prev_solution

def handcrafting_efficiency(product):
    recipe = recipes[product]
    return recipe.get("produced", 1) * 60.0 / (0.45 * recipe["build_steps"]) / recipe["rate"]

class FactorySolver2(FactorySolverBase):
    
    def solve(self):
        # initial solution - start by assigning as many machines as necessary to meet our constraints.
        solution = FactorySolution()
        solution.name = 'Start'
        for product, quantity in self.requirements.items():
            recipe = recipes[product]
            solution.machines[product] = math.ceil(quantity / (recipe['rate'] * self.constraints.max_time / 60.0))
            solution.automation_times[product] = math.ceil(60.0 * quantity / (solution.machines[product] * recipe['rate']))
            solution.automation_production[product] = quantity
            solution.handcrafting_times[product] = 0
            solution.handcrafting_production[product] = 0
        solution.compute_derived_values()
        solution.log_machines(self.requirements)
        
        solutions = [solution]
        iteration_index = 1
        while True:
            best_candidate = solutions[-1]
            
            log('')
            log(f'==== Starting iteration {iteration_index} =====')
            log(f'{best_candidate.machine_count} machines, {best_candidate.constructor_count} constructors')
            log('Initial time:')
            best_candidate.print_times()
            
            for product, count in best_candidate.machines.items():
                recipe = recipes[product]
                if count == 0 or recipe.get('build_steps', 0) == 0:
                    continue
                
                log('')
                log(f'Evaluating {product}')
                candidate_solution = copy.copy(solutions[-1])
                candidate_solution.name = product
                candidate_solution.automation_times[product] = self.constraints.max_time
                candidate_solution.machines[product] -= 1
                candidate_solution.automation_production[product] = math.floor(self.constraints.max_time * candidate_solution.machines[product] * recipe['rate'] / 60.0)
                needed = math.ceil(self.requirements[product] - candidate_solution.automation_production[product])
                time_to_craft = math.ceil(needed * 0.45 * recipe["build_steps"] / recipe.get("produced", 1))
                log(f'Reducing machines from {candidate_solution.machines[product] + 1} to {candidate_solution.machines[product]}')
                log(f'Excess {needed} items need to be manually produced in {ftime(time_to_craft)}, already producing {candidate_solution.handcrafting_production[product]} in {ftime(candidate_solution.handcrafting_times[product])}')
                candidate_solution.handcrafting_times[product] = time_to_craft
                candidate_solution.handcrafting_production[product] = needed
                candidate_solution.compute_derived_values()
                
                log(f'Auto {ftime(best_candidate.automation_times[product])} -> {ftime(candidate_solution.automation_times[product])} '
                    f'Manual {ftime(best_candidate.handcrafting_times[product])} -> {ftime(candidate_solution.handcrafting_times[product])}')
                delta = candidate_solution.handcrafting_time - best_candidate.handcrafting_time
                log(f'Total manual time {ftime(best_candidate.handcrafting_time)} -> {ftime(candidate_solution.handcrafting_time)} (delta {ftime(delta)})')                
                
                if candidate_solution.handcrafting_time > self.constraints.max_time:
                    log(f'Manual time of {ftime(candidate_solution.handcrafting_time)} exceeds max constraint of {ftime(self.constraints.max_time)}')
                else:
                    better = False
                    reason = ''
                    if candidate_solution.machine_count < best_candidate.machine_count:
                        better = True
                        reason = 'fewer machines'
                    elif candidate_solution.handcrafting_time < best_candidate.handcrafting_time:
                        better = True
                        reason = 'shorter time'
                    elif candidate_solution.handcrafting_time == best_candidate.handcrafting_time:
                        if handcrafting_efficiency(product) > handcrafting_efficiency(best_candidate.name):
                            better = True
                            reason ='better handcrafting efficiency'
                        elif handcrafting_efficiency(product) == handcrafting_efficiency(best_candidate.name):
                            log(f'{product} is tied with {best_candidate.name}')                       
                     
                    if better:   
                        log(f'Candidate {product} is better than prior best candidate {best_candidate.name} because of {reason}')
                        best_candidate = candidate_solution
                    # ties?
                    
            if best_candidate != solutions[-1]:
                log('')
                log(f'OUTCOME: {best_candidate.name} is winning candidate')
                log(f'Manual time {ftime(solutions[-1].handcrafting_time)} -> {ftime(best_candidate.handcrafting_time)}')
                solutions.append(best_candidate)
            else:
                log('No further improvement to solution.')
                break
            
            iteration_index += 1
                
                    
def init():
    log(f'Starting Satisfactory Solver @ {datetime.now()}')
    log('Loading game data.')
    with open("game_data.json") as f:
        data = json.load(f)
    for recipe in data["recipes"]:
        recipes[recipe["name"]] = recipe
    for project in data["projects"]:
        projects[project["name"]] = project
    for building in data["buildings"]:
        buildings[building["name"]] = building

def analyze():
    projects = [
        "Logistics",
        "Part Assembly",
        "Space Elevator",
        "Project Assembly Phase 1",
        "Coal Power",
    ]
    power = gather_power_requirements_projects(projects)

    biofuel = math.ceil(power / 450.0)
    biomass = math.ceil(power / 180.0)
    wood = math.ceil(biomass / 5.0)
    leaves = math.ceil(biomass * 2.0)
    log(f"Power requirement is {power / 1000.0:.1f} GJ ({biofuel} biofuel, {wood} wood, {leaves} leaves.")

    constraints = FactoryConstraints()
    constraints.conveyor_speed = 120
    # constraints.max_buildings['Iron Ingot'] = 3
    # constraints.max_buildings['Copper Ingot'] = 1
    # constraints.max_buildings["Concrete"] = 2
    constraints.max_time = 10 * 60

    # we are limited by the number of nodes in the vicinity, however we often have left over ore from idling,
    # and we can use portable miners to get around node limitations.
    # we can also feed smelters manually from portable miners.

    solver = FactorySolver2()
    solver.optimize_machines("Space Elevator", constraints)
    
    log_file.flush()


init()
analyze()
